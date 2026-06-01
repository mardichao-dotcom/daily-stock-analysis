# Stage 8 W1 開發回顧 — 設計準則與經驗

> 整理日期:2026-05-26
> 範圍:Stage 8 W1.1 ~ W1.5(scoring + standing 狀態機開發完整週期)
> 用途:作為 W2+ 開發的設計準則 baseline + 給未來新對話的接續依據

---

## 給未來新對話的提醒

進入 Stage 8 **W2+** 開發前,請讀以下 **4 份核心 canon 文件**:

1. **朋友規則_v2_1_final.md** — 規則層 FINAL,計分公式的唯一權威
2. **stage8_spec.md** — 開發任務書,週工作分解
3. **docs/stage8_pending_review.md** — 朋友 review 待辦,規則 vs v1 歧義紀錄
4. **本檔(stage8_w1_retrospective.md)** — W1 累積的設計準則,決策框架

外加實作層參考:
- `src/scoring/*.py` — 5 個 scoring 純函式模組(W1.4)
- `src/triggers/standing.py` — 站穩 / 跌破狀態機(W1.5)
- `migrations/001_standing_state.sql` — 持久化 schema
- `tests/test_*.py` — 141 個測試,既是驗證也是規格 by example

---

## 7 條設計準則(W1 累積,W2+ 沿用)

### 1. 純函式 + IO 隔離
所有 scoring / state machine 模組都不讀檔不查 DB,caller 自備 dict。
測試用 dict mock,跑 0.003 秒 141 個。
W2.x:IO 留在 state_io.py / run_filters_v2.py 主幹,scoring 函式不污染。

### 2. 嚴格模式(DD2)— silent fail 是 100 萬實盤最大的敵人
未知 adjective / color / category / level / state 一律 `raise ValueError`,
不靜默 fallback。拼錯字當場噴錯,比偷偷算錯分數安全得多。
W2.x:DB 讀寫的 schema mismatch 也要噴錯。

### 3. `weights.json` 是唯一參數來源
連 volume 的 1.6x / 2.0x 門檻、sector_linkage 的 3%/1.5x/60 都從 JSON 來,
程式碼沒有任何 hardcoded 規則常數。
W2.x:新加邏輯也走 JSON,絕不寫死。

### 4. 規則 §7「保留 vs 調整」判斷準則(W1.4 確立)
| 規則 §7 用詞 | Stage 8 行為 |
|---|---|
| 「全部保留,權重不變」 | 沿用 v1 |
| 「保留並調整」/「改名」 | 對齊規則文字,即使跟 v1 不同 |
| 「新增」 | 純照規則 |

判斷錯會偷分數。每動一個模組前必對照 §7。

### 5. 新檔並行不原地改(v1 退路)
`run_filters.py` → 保留為 v1,**新增** `run_filters_v2.py`。
試跑通才在 W4.2 把 `run_all.sh` 切到 v2,舊 v1 留 `_legacy` 後綴一段時間再砍。
W2.x 沿用:`prepare_charts.py` 也是新檔不原地改。

### 6. 持久化用 string-stable identifier
composite PK `(symbol, category, price_str)`,**不用 hash**(版本/平台/語言會漂)、
**不用 float PK**(浮點誤差)。`price_str` 是從 key_prices.json 原始字串,
線 = "5380"、區域 = "2130-2180"。converter 已輸出 TEXT。
W2.x:DB schema 嚴格遵循這個 identifier 設計。

### 7. `pending_review.md` 養成順手記錄
規則文字 vs v1 實作的歧義、字面 vs 語意的決策、待朋友 review 的問題,
全部累積到 `docs/stage8_pending_review.md`。試跑後一次性 review。
W2.x:碰到新的歧義繼續加。

---

## 5 處 spec 演進(從規劃到實作的調整)

| 項目 | spec 原寫法 | 實作後實際 |
|---|---|---|
| `weights.json` 結構 | spec §3.1 給的版本 | W1 演進 4 次:adjective add/mult、level_rank、volume tiers+label、sector trigger |
| Scoring 模組數 | spec §4.1 列 5 個 | 實作 4 個就夠(`adjustments.py` 併入 `formula.py`) |
| 站穩 3 階段偽碼 | spec §1.4 留「再給一天 or UNTRIGGERED?」未決 | W1.5 確立 C4 三天視窗(D0+D1+D2) |
| `sectors.json` 結構 | spec §3.2 含 name/leaders/members 重複 watchlist 資料 | 改成只存 `{"sector_name": "A"}` flat |
| 「(無)」線預設類別 | spec §3.3 預設為 key_price | 驗證通過,保留 |

額外發現:
- spec §1.7 異常成交量「取較大者」沒寫具體門檻數字 → 對齊規則 v2.1 §1-B 用 1.6x / 2.0x
- 11 處邊界 case(Q1-Q7 + Q5 strict + breakdown 限制 + CANCELLED→UNTRIGGERED + price_str 雙層分離)全在 W1 釐清

---

## 141 個測試分布

| 模組 | 測試數 | 主要覆蓋 |
|---|---|---|
| `tests/test_formula.py` | 23 | rule §2-C 6 個範例 + 10 邊界 + 7 pure compute |
| `tests/test_chip_etf.py` | 20 | 共識分流 / 連續 / 點火 / 上限 / details / 真實情境 |
| `tests/test_volume.py` | 14 | 門檻 / inclusivity / no stacking / v1 vs v2 對比 fixture |
| `tests/test_sector_linkage.py` | 21 | 發動三條件 / 評級門檻 / strict mode / evidence |
| `tests/test_given_price.py` | 29 | 4 線×3 色×形容詞 / 3 MA / 4 區域 / should_score / strict / real KP smoke test |
| `tests/test_standing.py` | 34 | 5 prev_state × ≥3 情境 / 旺矽 §3-B / Q1-Q5 / breakdown 限制 / round-trip / strict |
| **總計** | **141** | 跑完 0.003 秒 |

---

## W2 開工注意事項

### 性質完全不同
| 維度 | W1 純函式 | W2 整合 |
|---|---|---|
| 測試 | dict mock | 真 SQLite + 真 K 線 fixture |
| Debug | 立刻噴 | 整條管道追 |
| 失敗模式 | TypeError/ValueError | silent data corruption |
| 風險 | 邏輯錯 | 邏輯對但組裝錯 |

### 4 條 W2 注意事項

1. **W2.1 schema migration**:
   `sqlite3 kline.db < migrations/001_standing_state.sql` 一行建表。
   IF NOT EXISTS 重跑無副作用。

2. **W2.2 caller 必須做 `price_str` vs `given_price` 兩層分離**:
   - 持久化識別:`price_str = line["price"]`(線)或 `f"{low}-{high}"`(區域)
   - 數學比較:`given_price = float(price_str)`(線)或 `(low+high)/2`(區域)
   - 規則已寫進 `src/triggers/standing.py` module docstring

3. **W2.3 並行驗證重點**:
   v1 / v2 同日跑兩次,差異最大的會是 volume(5d→20d)跟
   sector_linkage(整個重寫)。volume.py 有 v1 vs v2 對比 fixture 可比對。

4. **絕對不碰 5A/5B 既有**:
   `tv_collect.mjs` / `daily_update.py` / `import_kline.py` /
   LaunchAgent / Discord / `status_writer.py` / `daily_supervisor.py`
   一律不動。standing_state 表在 kline.db 是「新增表」不影響既有 schema。

### 測試分層
- `state_io.py` 的 SQL helper:用 `:memory:` SQLite
- `run_filters_v2.py` 主幹:用預先建好的 fixture kline.db
- **絕對不要對 production kline.db 跑測試**(會污染狀態)

### W1 測試應 100% 凍結
W2.x 寫完後,W1 那 141 個測試應該完全不動繼續綠。
如果為了配合 W2 改了 W1 的程式碼,代表架構有問題,要先停下來討論。

---

*整理者:Claude Code(2026-05-26 W1 結案後)*
*下一階段:W2.1 — state_io.py + run_filters_v2.py 主幹*
