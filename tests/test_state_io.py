"""
test_state_io.py — standing_state CRUD 單元測試(W2.1 Phase 1)

覆蓋:
  1. init_schema:建表 + index + idempotent
  2. read_state:missing row 回 None、existing 回 dict
  3. write_state:新增、UPSERT 更新、None 欄位處理
  4. read_states_for_symbol:多 row 排序、空 list、symbol 隔離
  5. 多 row 隔離:不同 symbol / category / price_str 不互相干擾
  6. dict round-trip:write→read 後 dict 完全一致

執行:python3 -m unittest tests.test_state_io
"""
from __future__ import annotations
import os
import sqlite3
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.persistence import state_io


# ─────────────────────────────────────────────────────────────────────────────
class StateIoTestBase(unittest.TestCase):
    """每個 test 用 fresh :memory: SQLite(完全隔離)"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        state_io.init_schema(self.conn)

    def tearDown(self):
        self.conn.close()


# ─────────────────────────────────────────────────────────────────────────────
class TestInitSchema(StateIoTestBase):
    """schema 建立"""

    def test_table_exists(self):
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='standing_state'"
        )
        self.assertIsNotNone(cur.fetchone())

    def test_indexes_exist(self):
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name LIKE 'idx_standing_state_%' ORDER BY name"
        )
        names = [row[0] for row in cur.fetchall()]
        self.assertIn("idx_standing_state_state", names)
        self.assertIn("idx_standing_state_symbol", names)

    def test_idempotent(self):
        """重跑 init_schema 不報錯(CREATE IF NOT EXISTS)"""
        state_io.init_schema(self.conn)
        state_io.init_schema(self.conn)
        # 沒 exception 就過

    def test_primary_key_constraint(self):
        """duplicate composite key 應該觸發 IntegrityError"""
        self.conn.execute(
            "INSERT INTO standing_state VALUES "
            "('X', 'key_price', '100', 'UNTRIGGERED', NULL, NULL, '2026-05-26', NULL)"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO standing_state VALUES "
                "('X', 'key_price', '100', 'TRIGGERED', NULL, NULL, '2026-05-26', NULL)"
            )

    def test_upgrades_old_schema_with_last_evaluated_date(self):
        """W2-3:舊庫(無 last_evaluated_date 欄)跑 init_schema 自動 ALTER 升級。"""
        old = sqlite3.connect(":memory:")
        old.execute("CREATE TABLE standing_state ("
                    "symbol TEXT NOT NULL, category TEXT NOT NULL, price_str TEXT NOT NULL,"
                    "state TEXT NOT NULL, trigger_date TEXT, standing_date TEXT,"
                    "last_updated TEXT NOT NULL, PRIMARY KEY (symbol, category, price_str))")
        old.execute("INSERT INTO standing_state VALUES "
                    "('X','key_price','100','STANDING','2026-05-13','2026-05-14','t')")
        state_io.init_schema(old)                        # 應 ALTER 而非炸
        r = state_io.read_state(old, "X", "key_price", "100")
        self.assertEqual(r["state"], "STANDING")
        self.assertIsNone(r["last_evaluated_date"])      # 舊 row 補 NULL
        old.close()


# ─────────────────────────────────────────────────────────────────────────────
class TestReadState(StateIoTestBase):
    """read_state 行為"""

    def test_missing_row_returns_none(self):
        result = state_io.read_state(self.conn, "TPEX:6223", "inner_support", "4640")
        self.assertIsNone(result)

    def test_existing_row_returns_dict(self):
        state_io.write_state(
            self.conn, "TPEX:6223", "inner_support", "4640",
            {"state": "STANDING",
             "trigger_date": "2026-05-13",
             "standing_date": "2026-05-14"},
            last_updated="2026-05-14T19:00:00+08:00",
        )
        result = state_io.read_state(self.conn, "TPEX:6223", "inner_support", "4640")
        self.assertEqual(result, {
            "state":               "STANDING",
            "trigger_date":        "2026-05-13",
            "standing_date":       "2026-05-14",
            "last_evaluated_date": None,
        })

    def test_returned_dict_matches_evaluate_standing_input(self):
        """read_state 回的 dict 結構應該直接可餵給 evaluate_standing
        (W2-3 起多帶 last_evaluated_date;evaluate_standing 忽略多餘 key)"""
        state_io.write_state(
            self.conn, "X", "key_price", "100",
            {"state": "TRIGGERED", "trigger_date": "2026-05-13", "standing_date": None},
            last_updated="2026-05-13",
        )
        result = state_io.read_state(self.conn, "X", "key_price", "100")
        self.assertEqual(set(result.keys()),
                         {"state", "trigger_date", "standing_date", "last_evaluated_date"})

    def test_write_and_read_last_evaluated_date(self):
        """W2-3:last_evaluated_date 寫入/讀回(冪等判斷的依據)。"""
        state_io.write_state(
            self.conn, "X", "key_price", "100",
            {"state": "STANDING", "trigger_date": "2026-05-13", "standing_date": "2026-05-14"},
            last_updated="t", last_evaluated_date="2026-05-14",
        )
        r = state_io.read_state(self.conn, "X", "key_price", "100")
        self.assertEqual(r["last_evaluated_date"], "2026-05-14")


# ─────────────────────────────────────────────────────────────────────────────
class TestWriteState(StateIoTestBase):
    """write_state UPSERT 行為"""

    def test_insert_new_row(self):
        state_io.write_state(
            self.conn, "X", "key_price", "100",
            {"state": "UNTRIGGERED", "trigger_date": None, "standing_date": None},
            last_updated="2026-05-26",
        )
        cur = self.conn.execute("SELECT COUNT(*) FROM standing_state")
        self.assertEqual(cur.fetchone()[0], 1)

    def test_upsert_updates_existing_row(self):
        """write 同一個 (symbol, category, price_str) 兩次 → 仍只 1 row"""
        for state in ("UNTRIGGERED", "TRIGGERED", "STANDING"):
            state_io.write_state(
                self.conn, "X", "key_price", "100",
                {"state": state, "trigger_date": None, "standing_date": None},
                last_updated="2026-05-26",
            )
        cur = self.conn.execute("SELECT COUNT(*) FROM standing_state")
        self.assertEqual(cur.fetchone()[0], 1)
        # 最後狀態應該是 STANDING
        result = state_io.read_state(self.conn, "X", "key_price", "100")
        self.assertEqual(result["state"], "STANDING")

    def test_upsert_updates_all_fields(self):
        """UPSERT 應該更新所有 5 個欄位"""
        state_io.write_state(
            self.conn, "X", "key_price", "100",
            {"state": "UNTRIGGERED", "trigger_date": None, "standing_date": None},
            last_updated="2026-05-26T08:00:00",
        )
        state_io.write_state(
            self.conn, "X", "key_price", "100",
            {"state": "STANDING",
             "trigger_date": "2026-05-13",
             "standing_date": "2026-05-14"},
            last_updated="2026-05-27T08:00:00",
        )
        # 完整 row 驗證
        cur = self.conn.execute(
            "SELECT state, trigger_date, standing_date, last_updated "
            "FROM standing_state WHERE symbol='X'"
        )
        row = cur.fetchone()
        self.assertEqual(row, ("STANDING", "2026-05-13", "2026-05-14",
                               "2026-05-27T08:00:00"))

    def test_write_with_none_dates(self):
        """trigger_date / standing_date 可以是 None(UNTRIGGERED 狀態)"""
        state_io.write_state(
            self.conn, "X", "key_price", "100",
            {"state": "UNTRIGGERED", "trigger_date": None, "standing_date": None},
            last_updated="2026-05-26",
        )
        result = state_io.read_state(self.conn, "X", "key_price", "100")
        self.assertIsNone(result["trigger_date"])
        self.assertIsNone(result["standing_date"])

    def test_write_missing_optional_keys(self):
        """state_dict 沒帶 trigger_date / standing_date → 視為 None"""
        state_io.write_state(
            self.conn, "X", "key_price", "100",
            {"state": "UNTRIGGERED"},   # 只有 state,沒 trigger_date / standing_date
            last_updated="2026-05-26",
        )
        result = state_io.read_state(self.conn, "X", "key_price", "100")
        self.assertIsNone(result["trigger_date"])
        self.assertIsNone(result["standing_date"])


# ─────────────────────────────────────────────────────────────────────────────
class TestReadStatesForSymbol(StateIoTestBase):
    """read_states_for_symbol 行為"""

    def test_empty_symbol_returns_empty_list(self):
        result = state_io.read_states_for_symbol(self.conn, "TPEX:6223")
        self.assertEqual(result, [])

    def test_multiple_rows_returned_sorted(self):
        """單檔多條 line / area → 依 (category, price_str) 排序"""
        items = [
            ("inner_support", "4640"),
            ("inner_support", "3580"),
            ("key_price",     "6285"),
            ("order_block",   "5025-5370"),
        ]
        for category, price_str in items:
            state_io.write_state(
                self.conn, "TPEX:6223", category, price_str,
                {"state": "UNTRIGGERED", "trigger_date": None, "standing_date": None},
                last_updated="2026-05-26",
            )
        result = state_io.read_states_for_symbol(self.conn, "TPEX:6223")
        # 排序預期: inner_support 3580 → inner_support 4640 → key_price 6285 → order_block 5025-5370
        self.assertEqual(
            [(r["category"], r["price_str"]) for r in result],
            [("inner_support", "3580"),
             ("inner_support", "4640"),
             ("key_price",     "6285"),
             ("order_block",   "5025-5370")],
        )

    def test_symbol_isolation(self):
        """不同 symbol 不互相干擾"""
        state_io.write_state(
            self.conn, "TPEX:6223", "key_price", "100",
            {"state": "STANDING", "trigger_date": "X", "standing_date": "Y"},
            last_updated="X",
        )
        state_io.write_state(
            self.conn, "TWSE:2330", "key_price", "100",
            {"state": "UNTRIGGERED", "trigger_date": None, "standing_date": None},
            last_updated="X",
        )
        r1 = state_io.read_states_for_symbol(self.conn, "TPEX:6223")
        r2 = state_io.read_states_for_symbol(self.conn, "TWSE:2330")
        self.assertEqual(len(r1), 1)
        self.assertEqual(len(r2), 1)
        self.assertEqual(r1[0]["state"], "STANDING")
        self.assertEqual(r2[0]["state"], "UNTRIGGERED")


# ─────────────────────────────────────────────────────────────────────────────
class TestCompositeKeyIsolation(StateIoTestBase):
    """composite PK (symbol, category, price_str) 各維度隔離"""

    def test_same_symbol_different_category(self):
        """同 symbol 不同 category → 兩 row"""
        state_io.write_state(self.conn, "X", "key_price", "100",
                              {"state": "UNTRIGGERED"}, last_updated="X")
        state_io.write_state(self.conn, "X", "inner_support", "100",
                              {"state": "STANDING", "trigger_date": "Y"},
                              last_updated="X")
        cur = self.conn.execute("SELECT COUNT(*) FROM standing_state WHERE symbol='X'")
        self.assertEqual(cur.fetchone()[0], 2)

    def test_same_symbol_category_different_price(self):
        """同 symbol+category 不同 price_str → 兩 row(朋友改價會走這路徑)"""
        state_io.write_state(self.conn, "X", "key_price", "100",
                              {"state": "STANDING", "trigger_date": "A"},
                              last_updated="X")
        state_io.write_state(self.conn, "X", "key_price", "150",
                              {"state": "UNTRIGGERED"}, last_updated="X")
        cur = self.conn.execute("SELECT COUNT(*) FROM standing_state WHERE symbol='X'")
        self.assertEqual(cur.fetchone()[0], 2)
        # 兩個獨立狀態:朋友把關鍵價從 100 改 150,舊狀態保留,新的從 UNTRIGGERED
        result_100 = state_io.read_state(self.conn, "X", "key_price", "100")
        result_150 = state_io.read_state(self.conn, "X", "key_price", "150")
        self.assertEqual(result_100["state"], "STANDING")
        self.assertEqual(result_150["state"], "UNTRIGGERED")


# ─────────────────────────────────────────────────────────────────────────────
class TestRoundTrip(StateIoTestBase):
    """write → read 後 dict 完全一致(給 W2.2 evaluate_standing 接 IO 的前置驗證)"""

    def test_all_5_states_round_trip(self):
        states = ["UNTRIGGERED", "TRIGGERED", "STANDING", "MAINTAINING", "CANCELLED"]
        for i, s in enumerate(states):
            state_dict = {
                "state":         s,
                "trigger_date":  f"2026-05-{13+i:02d}",
                "standing_date": f"2026-05-{14+i:02d}",
            }
            state_io.write_state(self.conn, "X", "cat", str(i),
                                  state_dict, last_updated=f"2026-05-{14+i:02d}")
            result = state_io.read_state(self.conn, "X", "cat", str(i))
            # W2-3 起 read 多帶 last_evaluated_date(未傳則 None)
            self.assertEqual(result, {**state_dict, "last_evaluated_date": None})
