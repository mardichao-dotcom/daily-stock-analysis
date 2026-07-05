"""
test_fetch_weekly.py — stage9 Day3 §3.3 週報純函式(警報閾值 config 化、MA 交叉、NAAIM 解析)
"""
from __future__ import annotations
import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import fetch_weekly as fw
from src import naaim


class TestMA(unittest.TestCase):
    def test_ma_windows(self):
        s = [1, 2, 3, 4, 5]
        ma3 = fw._ma(s, 3)
        self.assertEqual(ma3[:2], [None, None])
        self.assertAlmostEqual(ma3[2], 2.0)   # (1+2+3)/3
        self.assertAlmostEqual(ma3[4], 4.0)   # (3+4+5)/3


class TestAlertsConfigDriven(unittest.TestCase):
    """警報閾值一律讀 config,不寫死。"""
    CFG = {"naaim": {"extreme_high": 90, "extreme_low": 20},
           "vix": {"high": 25}}

    def test_naaim_extreme_high(self):
        a = fw.build_alerts(95, {"value": 16}, {"cross": "none"}, self.CFG)
        self.assertTrue(any("過度樂觀" in x for x in a))

    def test_naaim_extreme_low(self):
        a = fw.build_alerts(15, {"value": 16}, {"cross": "none"}, self.CFG)
        self.assertTrue(any("過度悲觀" in x for x in a))

    def test_naaim_normal_no_alert(self):
        a = fw.build_alerts(84.69, {"value": 16}, {"cross": "none"}, self.CFG)
        self.assertEqual([x for x in a if "NAAIM" in x], [])

    def test_vix_high(self):
        a = fw.build_alerts(50, {"value": 30}, {"cross": "none"}, self.CFG)
        self.assertTrue(any("VIX" in x and "恐慌" in x for x in a))

    def test_death_cross(self):
        a = fw.build_alerts(50, {"value": 16}, {"cross": "death"}, self.CFG)
        self.assertTrue(any("死亡交叉" in x for x in a))

    def test_threshold_respects_config(self):
        # 自訂較低門檻 → 84.69 就觸發
        cfg = {"naaim": {"extreme_high": 80, "extreme_low": 20}, "vix": {"high": 25}}
        a = fw.build_alerts(84.69, {"value": 16}, {"cross": "none"}, cfg)
        self.assertTrue(any("過度樂觀" in x for x in a))


class TestNaaimParse(unittest.TestCase):
    def test_parse_picks_date_and_mean(self):
        # 用 openpyxl 造一個小 xlsx in-memory 驗證欄位對應
        import io, openpyxl
        from datetime import datetime
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["Date", "Mean/Average", "Most Bearish", "NAAIM Number"])
        ws.append([datetime(2026, 7, 1), 84.69, 0, 84])
        ws.append([datetime(2026, 6, 24), 98.59, 0.2, 98])
        buf = io.BytesIO(); wb.save(buf)
        data = naaim.parse_history(buf.getvalue())
        self.assertEqual(dict(data)["2026-07-01"], 84.69)
        self.assertEqual(dict(data)["2026-06-24"], 98.59)

    def test_parse_dedups_by_date(self):
        import io, openpyxl
        from datetime import datetime
        wb = openpyxl.Workbook(); ws = wb.active
        ws.append(["Date", "Mean/Average"])
        ws.append([datetime(2006, 7, 5), 19.44])
        ws.append([datetime(2006, 7, 5), 19.44])   # dup
        buf = io.BytesIO(); wb.save(buf)
        data = naaim.parse_history(buf.getvalue())
        self.assertEqual(len(data), 1)


class TestRenderWeekly(unittest.TestCase):
    def setUp(self):
        from src import render_weekly as rw
        self.rw = rw
        self.cfg = {"naaim": {"extreme_high": 90, "extreme_low": 20}, "vix": {"high": 25}}

    def _data(self, **over):
        d = {"generated_at": "2026-07-05T09:00:00+08:00", "data_through": "2026-07-05",
             "errors": [], "alerts": [],
             "naaim": {"status": "ok", "latest_date": "2026-07-01", "latest_value": 84.69,
                       "count": 1043, "series": {"dates": [], "exposure": []}},
             "vix": {"value": 16.15}, "xly_xlp": {"ratio": 1.378, "cross": "none", "trend": "risk_off"},
             "margin": {"total": 12089437, "wow_pct": None}, "taiex": {"close": 46780.62, "week_change_pct": 4.96}}
        d.update(over); return d

    def test_render_has_key_sections(self):
        html = self.rw.render(self._data(), self.cfg, False, False)
        for frag in ["每週市場情緒週報", "NAAIM", "VIX", "XLY/XLP", "84.69", "46,780" ]:
            self.assertIn(frag, html)
        self.assertIn("本週無極端訊號", html)      # 0 alerts → 綠色 noalert

    def test_render_na_guardrail(self):
        # NAAIM 失敗 → 顯示 N/A,不冒充舊值
        d = self._data(naaim={"status": "N/A", "error": "boom"},
                       errors=["naaim: boom"])
        html = self.rw.render(d, self.cfg, False, False)
        self.assertIn("N/A", html)
        self.assertIn("部分數據源失敗", html)

    def test_render_alerts_shown(self):
        d = self._data(alerts=["🔴 NAAIM 曝險 95 > 90:機構過度樂觀,逆向警戒"])
        html = self.rw.render(d, self.cfg, False, False)
        self.assertIn("過度樂觀", html)
        self.assertNotIn("本週無極端訊號", html)


if __name__ == "__main__":
    unittest.main()
