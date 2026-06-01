# 2026-05-28 接續摘要 v4 — Stage 8 W1 + W2 全完成

> 上一個對話完成 W1 (scoring 純函式 + standing 狀態機),本次對話完成 W2 (backend pipeline)。
> 下一階段:W3 前端(需先決定區塊結構)。
> 上游文件:`朋友規則_v2_1_final.md`、`key_prices_clean_v3.md`、`stage8_spec.md`、
>          `docs/stage8_w1_retrospective.md`、`docs/stage8_pending_review.md`

---

## 🎯 今天(5/28)做了什麼

### W2 全部 4 個 phase 完成 + 對比驗證

| Phase | 範圍 | 新增測試 |
|---|---|---|
| **W2.1** | `state_io.py` + `run_filters_v2.py` 主幹 + 旺矽 8 天 smoke | 33 |
| **W2.2.1** | chip_etf 接入 + `etf_io.py` + 6 v1 parity | 24 |
| **W2.2.2** | volume 接入 + `kline_io.py` + `data_date_in_db` | 15 |
| **W2.2.3** | sector_linkage + 全市場 activations cache | 12 |
| **W2.2.4** | MA 接入 + **MA120 → MA90 全面修正(21 處)** | 15 |
| **W2.2.5** | grader floor 規則(5.5→A、6.0→S) | 15 |
| **W2.2.6** | rotation + `score_history.db` + 巢狀 GROUP BY date | 17 |
| **W2.2.7** | MACD strict 50 天 + 連續 2 天確認 | 24 |
| **W2.3** | `tools/compare_v1_v2.py` v1/v2 並行驗證 + 朋友 review 單頁 | (一次性工具) |
| **W2.4** | `prepare_charts_v2.py` chart producer + events 從 K 線重算 | 23 |
| **W2 共計** | — | **+178(W1 141 → W2 結束 322)** |

**322 個測試全綠,0.064 秒跑完,旺矽 8 天 fixture 跨 13 phase byte-for-byte 不變。**

---

## 🔑 重大決策(影響規則行為,試跑時要說明)

### 1. MA120 → MA90(資料修正,21 處)
- 朋友規則最初寫「120 日均線」是打錯,實際台股慣用「20/60/90」
- 90 = 季線(跟台股「季」習慣對齊)
- weights / rules / 程式 / 測試全面改 ma_90

### 2. MA 計分機制 = 首次站上 +N(非每天 +N)
- 規則 §1-D 寫「動態」但沒寫首次 vs 每天
- 為什麼選首次:避免「每天 +5 → D 級保底」分數失焦
- 簡化機制:只記 prev_above boolean、從 kline_history 重算(不寫 standing_state)
- **長榮 5/20 案例**:首次站上三條 +5 衝 S 級 → W2.3 朋友 review 重點

### 3. MACD 動能轉換(2026-05-29 規格修訂 — 朋友 review 後)
- **動能轉多**(OSC 由負轉正):**計分 +1** + tag「⚡ MACD 動能轉多(買點)」
- **動能轉空**(OSC 由正轉負):純標籤「⚡ MACD 動能轉空」(不計分)
- 偵測改「當天就報」(2 根 OSC 跨零軸,原連續 2 天確認易漏買點)
- 標籤文字用「動能轉多/轉空」**避免台股 vs TradingView 顏色相反混淆**
- 程式內部仍保留 `green_to_red` / `red_to_green` 字串(維護成本低)
- 50 天暖機保留(EMA26 + EMA9 + 穩定 buffer)
- **流程教訓**:朋友口頭規格 vs 規則文件 3 次落差(計分/即時/顏色),
  未來規則改動先回寫文件再改程式

⚠️ W2.3 對比報告是「MACD 標籤版」產出(2026-05-28 之前的版本),
   2026-05-29 後 MACD 改為「動能轉多計分版」,報告中 ⚡ MACD tag 統計仍正確
   但分數對比可能有微調。重跑工具可重產報告。

### 4. Rotation 巢狀 GROUP BY date
- 「先各日算族群均分,再 5 日平均」(`SELECT AVG(daily_avg)`)
- 不是「所有 row 直接 AVG」(會被停牌成員稀釋)
- score_history.db 新增,trigger threshold delta ≥ 2(inclusive)

### 5. Events 從 K 線重算(不從 DB 撈)
- prepare_charts_v2 用「今天的 key_prices」對過去 180 天 K 線重跑狀態機
- 揭露完整歷史(長榮 16 個 events = 6 standing + 10 breakdown)
- 跟 standing.py 純函式設計一脈相承

### 6. 規則層發動只算漲(語意決策)
- 規則字面「abs(漲跌) > 3%」改為「漲幅 > 3%」
- 跌 > 3% + 爆量是恐慌出貨,不該觸發族群連動加分
- 跌訊號由 §4「🔴 跌破標籤」處理

### 7. price_str 雙層分離(W1.5 確立 + W2 全面實踐)
- 持久化識別:`price_str = line["price"]` 字串(避免浮點漂)
- 數學比較:`given_price = float(line["price"])` float
- standing_state composite PK `(symbol, category, price_str)` 全 string-stable

---

## 📊 W2.3 v1/v2 對比驗證結果

**技術面:0 v2 bug。** chip_etf parity 5 天 100%、36 個 |diff|≥2 全部歸因。

**3 個給朋友決定的規則設計後果**(見 `tools/compare_output/朋友_review_重點.md`):

1. **純技術突破股變 0 分(規則 §6 砍 K 棒型態)**
   - 國巨 5/20:漲停 +2、突破 60 日高 +2、跳空 +1、強紅 K +1 → v2 給 0 分
   - 朋友選項:全砍 / 只保留突破 60 高 / 保留 2 個 / 全部恢復

2. **MA 首次站上佔比過大(W2.2.4 後果)**
   - 長榮 5/20:v2=7(S 級)、MA 貢獻 +5(71%)、單軸決定 S 級
   - 朋友選項:接受 / 降 ma_90 權重 / 加 cooldown / 三條全站才給分

3. **v1 ETF 減碼壓分 → v2 不壓**(13 檔)
   - 純加分制結構差
   - 朋友選項:接受 / 新增「⛔ ETF 減碼」純標籤 / 回到雙向

---

## 📁 完整檔案清單(W2 新增)

### 計分核心
```
src/scoring/
├── formula.py             — 公式核心(色×base×形容詞)+(重要)
├── chip_etf.py            — ETF 籌碼(沿用 v1)
├── volume.py              — 異常成交量(對齊規則 v2.1 升級)
├── sector_linkage.py      — 族群連動(完全重寫,只算漲)
├── given_price.py         — 9 種給定價格類別
├── grader.py              — S/A/B/C/D 分級(floor)
├── macd.py                — MACD 紅綠轉換(純標籤)
src/triggers/
└── standing.py            — 站穩 / 跌破狀態機(5 狀態)
src/persistence/
├── state_io.py            — standing_state CRUD
├── etf_io.py              — etf_operations.db 讀取
├── kline_io.py            — kline.db helpers
└── score_history_io.py    — score_history CRUD + rotation 查詢
```

### Pipeline
```
src/run_filters_v2.py      — 主幹(scoring + state + IO + rotation tags)
src/prepare_charts_v2.py   — chart JSON producer(events 從 K 線重算)
```

### Migrations
```
migrations/
├── 001_standing_state.sql  — W1.5 站穩狀態持久化
└── 002_score_history.sql   — W2.2.6 每日分數歷史
```

### Tools(驗證用)
```
tools/compare_v1_v2.py            — v1/v2 並行對比工具(可重跑)
tools/compare_output/             — 5 天詳報 + 朋友 review 單頁
tools/convert_key_prices.py       — md → JSON (W1.2)
```

### Tests(322 個)
```
tests/test_formula.py             — 23
tests/test_chip_etf.py            — 20
tests/test_volume.py              — 14
tests/test_sector_linkage.py      — 21
tests/test_given_price.py         — 29
tests/test_standing.py            — 34
tests/test_state_io.py            — 18
tests/test_etf_io.py              — 18
tests/test_kline_io.py            — 3
tests/test_score_history_io.py    — 12
tests/test_macd.py                — 19
tests/test_grader.py              — 15
tests/test_run_filters_v2.py      — 73
tests/test_prepare_charts_v2.py   — 23
                                  ─────
                                 = 322
```

### Configs
```
config/weights.json        — 計分權重(含 grade thresholds + level_rank)
config/sectors.json        — 16 個台股板塊評級(全 A 暫定)
config/key_prices.json     — 88 檔朋友手繪關鍵價
config/visual.json         — chart 視覺規則(顏色/線型)
```

---

## ⏳ 下一步:W3 前端(尚未開工)

### 系統定位(2026-05-29 確立)

**Stage 8 系統定位 = 「找買點的雷達 + ETF 籌碼面雙向掃描」**

- 不是「綜合評估儀表板」、不是「個股研究平台」
- 純為「**朋友每天盤後 5-10 分鐘掃一輪,看哪些股票今天值得進場(或被砸貨要避開)**」設計
- 設計哲學:**找「即將發動」的訊號組合,不是事後追漲**
- 主軸 6 個訊號:籌碼共識買 + 量比 + 族群連動 + 關鍵價站穩 + MACD 動能轉多 + ⛔ ETF 減碼風險提醒

### 朋友 review 結果(2026-05-29 已確認)

| 重點 | 朋友決定 |
|---|---|
| 1. 純技術突破股變 0 分 | ✅ **維持** — 雷達不找「已發動」 |
| 2. MA 首次站上佔比過大(長榮) | ✅ **維持** — 多空轉折強訊號,S 級正確 |
| 3. v1 ETF 減碼 → v2 不壓 | ✅ **維持純加分制 + 新增 ⛔ 減碼純標籤**(2026-05-29 已實作)|
| 4. MACD 純標籤 vs 動能轉多計分 | 🔧 **改為動能轉多 +1**(2026-05-29 已實作)|

### W3 backend 7 日窗口確認(2026-05-31 完成)

**這是 W3 階段的重要里程碑** — backend schema 100% 就緒,可進前端。

| 維度 | 狀態 |
|---|---|
| `filtered_result_v2.json` schema | 含 stocks[*]{name, sector} + etf_active{increase, decrease} |
| 6 ETF 範圍規則(2025 績效精選池 + 00403A)| 朋友規則 §1-A 已明寫 |
| 7 日窗口共識邏輯(跟 chip_etf 一致)| 朋友規則 §1-A 已明寫 |
| ⛔ 減碼純標籤例外用「當天」| 個股卡即時提醒 |
| chip_etf parity 5 天 100% | ✓ |
| 旺矽 8 天 fixture 不變承諾跨 14 phase | ✓ |
| 測試 | **335 全綠** |

⚠️ **用戶要做的**(不是 Claude 動):
- 手動改 `~/ETF追蹤/daily_update.py:14` 加 `'00403A'`
- 決定是否補抓 5/12-5/30 的 00403A 歷史 operations

### W3 主體規劃(明天開工,5-6 小時)

**區塊數已決定:6 區塊**(2026-05-29 確認,原 5 區塊 + ETF 主動式)

```
↓ 區塊 1 🏆 當日前十名(降冪)
↓ 區塊 2 🔴 S 級戰區(≥ 6 分)— 含 K 線
↓ 區塊 3 🟡 A 級戰區(= 5 分)— 含 K 線
↓ 區塊 4 🟢 B 級戰區(= 4 分)— 含 K 線
↓ 區塊 5 ⛔ ETF 主動式(雙向掃描,近 7 日累計窗口)
   - 共識加碼 ≥ 2 檔:已在計分內(S/A/B 級)
   - 共識減碼 ≥ 2 檔:本區塊文字摘要(高亮提醒)
   - ⚠️ 窗口期定為 7 日累計(2026-05-31 朋友確認):
     解決 etfedge 不保證每日即時更新的延遲問題
↓ 區塊 6 ⭐ C 級以下特殊標籤(動能轉多 / 站穩 / 跌破 / 輪動)
```

**W3 主體 4 件事(明天)**:
1. `src/render_v2.py` + 6 區塊 Jinja2 模板
2. `assets/chart_v2.js` — Lightweight Charts(OHLCV+MA+關鍵價+區域+ETF箭頭+events)
3. `docs/data/v2/{date}/_index.json` lazy load(card 展開才 fetch)
4. `visual.json` 樣式注入 — chart.js 載入時讀,設色彩 / 線型

### Pending review 剩 1 條(不阻塞,試跑後再決定)

- **MACD 暖機 50 天**:朋友覺得合理嗎?要更嚴格 78 天或寬鬆 36 天?
  試跑時讓朋友知道:他 TradingView 看的是暖機完的 MACD,系統 < 50 天沒 tag
  (其他 pending_review 條目已隨「維持現狀」+「MACD 修訂」+「⛔ 減碼標籤」消化)

---

## 🚨 完整 pending_review 條目(11 條)

朋友 review 試跑後決定:

**主要規則決策(待確認)**:
1. 族群連動重寫(v1 → v2.1 概念完全換)+ 跳空開高/異常點火/漲幅>7% 是否納回
2. MACD 連續 2 天確認 vs 當天觸發
3. MACD strict 50 天暖機(36 / 50 / 78 三選一)
4. MA 用 20/60/90 (不是 120)— 確認朋友 OK
5. MA 計分首次站上 +N(vs 每天 +N)
6. 異常成交量升級 5d/1.5→20d/1.6 是否合理
7. 連續加碼「同檔」字面 vs v1 個股維度
8. 異常點火「恰好 1 檔」vs「任何 1 檔」
9. 連續加碼 7 日窗口:自然日(v1)vs 交易日

**W2.3 朋友 review 單頁的 3 個重點**(`tools/compare_output/朋友_review_重點.md`):
- 純技術突破股變 0 分(規則 §6 後果)
- MA 首次站上佔比過大(長榮案例)
- v1 ETF 減碼壓分,v2 不壓

---

## 🚨 系統架構底線(再次確認,跨對話穩定)

- 本機 SQLite 唯一 ETF 資料源(`~/ETF追蹤/etf_operations.db`)
- **拒絕任何外部 ETF 線上查詢工具(etfedge MCP 禁用)**
- 升級 v1 → v2(不並存,W4 切換)
- 5A/5B 既有(tv_collect / daily_update / LaunchAgent / Discord)**完全不動**
- 純加分制,個股分數恆 ≥ 0
- v1 在凍結期間(W4 前)**不該被 import**

---

## 📋 git status(未 commit 的檔)

未追蹤(W1+W2 工作)需要進 git:
- `migrations/` `src/persistence/` `src/scoring/` `src/triggers/` `tests/` `tools/`
- `src/prepare_charts_v2.py` `src/run_filters_v2.py`
- `config/key_prices.json` `config/sectors.json` `config/visual.json` `config/weights.json`
- `朋友規則_v2_1_final.md` `key_prices_clean_v3.md` `2026-05-25_continuity_v3.md`
- `docs/stage8_pending_review.md`、`docs/stage8_w1_retrospective.md`

已追蹤但有改動(W1+W2 修):
- `run_all.sh`(5B CDP 自救,前面對話改的)
- `state/signal_state.json`(W2.3 compare tool 跑 v1 留下,已 restore 但 mtime 更新)

⚠️ 用戶未來自己決定何時 commit。

---

## 🚨 新對話開頭範本(更新版)

```
接續上個對話。專案 daily-stock-analysis(台股盤後決策儀表板)。

⚠️ Stage 8 W2 全完成(2026-05-28),322 測試全綠,backend pipeline 完整。
   下一階段 W3 前端,但開工前要先決定「8 區塊 vs 5 區塊」(我還在想)。

【先讀 4 份 canon 文件】
1. 朋友規則_v2_1_final.md           ⭐⭐⭐ 規則層 FINAL
2. stage8_spec.md                   ⭐⭐⭐ 開發任務書
3. docs/stage8_w1_retrospective.md  ⭐⭐  W1 設計準則
4. docs/2026-05-28_continuity_v4.md ⭐⭐  本檔(W2 完成接續)

【7 大決策(已實作,試跑時跟朋友說明)】
1. MA120 → MA90(資料修正)
2. MA 首次站上 +N(不是每天加)
3. MACD 連續 2 天確認 + strict 50 天暖機
4. Rotation 巢狀 GROUP BY date
5. Events 從 K 線重算
6. 族群連動只算漲(不算 abs)
7. price_str 雙層分離(string-stable 持久化)

【現況】
✅ W1+W2 全完成,322 測試
⏳ W3 開工前先決定區塊數
🚨 朋友 review 重點見 tools/compare_output/朋友_review_重點.md

【架構底線】本機 SQLite ETF 唯一資料源,拒絕外部線上 ETF 工具。
5A/5B 既有完全不動,v1 凍結到 W4 才切換。

開始接續。
```

---

## 🔮 上線後 TODO(2026-05-31 用戶提醒)

- **重審自動化流程**(舊網站「到後面自己無法更新」的痛點正在發生:
  `kline.db` 11 天沒跑就是例子)
- 詳見 `docs/上線後待辦.md`
- ⚠️ **chat 接續時看到這條,記得提醒用戶**

---

*整理者:Claude Code(2026-05-28,W2 結案後,2026-06-01 補上線後 TODO)*
*下一階段:W3 前端 — 開工前等用戶決定區塊數*
