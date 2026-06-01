# Stage 8 W3 Demo 完工(2026-06-01)

> 接續 `docs/2026-05-28_continuity_v4.md`(W2 結案)。
> W3 前端 + demo 端到端驗證完成,下一階段:Polish #2/#3/#5 + Watchlist v2 + git commit。

---

## 完成狀態

### ✅ W3 backend(335 tests)
- `score_one_symbol()` 回傳含 `name` / `sector`
- `etf_active` 進 top-level schema(`{increase: [...], decrease: [...]}`)
- ETF 共識窗口期:**7 日累計**(對齊 `chip_etf` 計分視角,規則文件 §1-A 已寫)
- ⛔ ETF 減碼純標籤例外用「**當天**」(個股卡即時提醒)

### ✅ W3 主體(350 tests)
- `src/render_v2.py`:7 區塊 HTML 產生器(top10 / S / A / B / C 特殊 / ETF 主動式 / 其餘)
- `docs/assets/chart_v2.js`:Lightweight Charts CDN lazy load,K 棒+MA+關鍵價+events+ETF 箭頭
- `docs/assets/style_v2.css`:響應式 CSS(768px / 480px 兩斷點)+ `chart-legend` 圖標說明
- `docs/data/v2/visual.json`:配色 + 線型 + `etf_arrow_buy` / `etf_arrow_sell`
- 純 Python 字串拼接(不用 Jinja2,跟 v1 一致)

### ✅ W3 demo 端到端驗證(雙日 demo)
- **5/20 baseline**:S 級 1 檔(長榮 7.0),`docs/data/v2/2026-05-20/`
- **5/19 ETF 箭頭 demo**:B 級國巨 4.0 + 59 筆 ETF events(▲ 49 / ▼ 10),
  `docs/data/v2/2026-05-19/`
- 兩天並存不衝突,切換指令見下方「Demo 使用方式」

### ✅ 4 個 bug 修補
| # | 症狀 | Root cause | 檔案:行 |
|---|---|---|---|
| 1 | K 線高度 0(細條) | `createChart` 在 `appendChild` 之前 → `clientWidth=0` | `chart_v2.js:96` |
| 2 | 彩色細紋(頂部) | priceLine `title` fallback 用 `line.category` = "key_price" | `chart_v2.js:164` |
| 3 | hover tooltip 沒顯示 | LWC 把字串 time 轉成 `BusinessDay {y,m,d}` 物件,`===` 字串永不匹配 | `chart_v2.js:289` |
| 4 | tooltip scroll 後位置跑掉 | `position:absolute` + viewport rect(沒加 `scrollX/Y`) | `chart_v2.js:295` |

### ✅ Polish #4 用詞
- header「**產出** → **產出時間**」(`render_v2.py:332`)

### ✅ 規則文件回寫(`朋友規則_v2_1_final.md`)
- §1-A:6 檔 ETF 精選池表 + 共識窗口期 7 日定義
- §1-A:行欄位「近 7 日累計 ≥ 2 檔 / ≥ 4 檔」
- §0 / §4:⛔ ETF 減碼純標籤(當天)
- 系統定位:「**找買點的雷達 + ETF 籌碼面雙向掃描**」

### ✅ 5A 範圍(用戶 2026-05-31 手動)
- `~/ETF追蹤/daily_update.py:14` 加 `'00403A'`
- 6 檔精選池正式湊齊(歷史 backfill 待上線後做,見 `docs/上線後待辦.md` §2)

---

## 還沒做(下次接續)

### Polish #2:計分明細 by-module 合併(版本 A)
- 目前:`+1.0 ma  首次站上 MA20` / `+2.0 ma  首次站上 MA60` / `+2.0 ma  首次站上 MA90` 三行
- 目標:`關鍵價/MA  +5.0  首次站上 MA20/60/90 (close 212.5 > 205.78 / 204.91 / 199.99)` 一行
- ⚠️ **合併規則**:照「該天 details 內實際有的 module」合併,不是寫死 20/60/90
  全部都合(如果只有站上 MA60,就只合 MA60)
- ⚠️ **空模組要顯示**(`族群 —` / `籌碼 —`)讓朋友看出「沒得分」
- 模組順序:量能 / 關鍵價 MA / 族群 / 籌碼 / MACD

### Polish #3:C 級按標籤分組(版本 C)
- 目前:21 檔平鋪 `<li>名稱 代號 分數 標籤</li>`
- 目標:按標籤類別分組
  ```
  🟢 站穩 (8 檔): A / B / C ...
  🔴 跌破 (3 檔): X / Y / Z ...
  ⚡ MACD (5 檔): ...
  個股輪動 (3 檔): ...
  ⛔ ETF 減碼 (2 檔): ...
  ```
- ⚠️ **同檔多標籤不去重**(訊號疊加 = 多次出現是 feature 不是 bug)

### Polish #5:其餘品項(版本 A)
- 目前:36 檔直排 `<li>`
- 目標:CSS `columns: 3` 自動分 3 欄(`docs/assets/style_v2.css:.other-list` 已是 3,
  確認手機 768 / 480 斷點 2 / 1 欄已正確)

### Watchlist v2 頁面(2026-06-01 凌晨用戶提)
- 全 59 檔 watchlist 都可折疊看 K 線
- 不是「每天 dashboard」,是「完整名單檢視」
- 用 `prepare_charts_v2 --all-watchlist` 或新指令一次產所有 chart JSON
- 新 HTML 頁面 `docs/watchlist_v2.html`,套用同一個 `chart_v2.js` 載入器

### git commit 累積 W2+W3 改動
- W2.3 / W2.4 / W3 backend / W3 主體 + 4 bug 修
- 還沒 commit,改動量大,建議切 `feat/stage8-w3` 分支

---

## 上線後待辦

詳見 `docs/上線後待辦.md`,3 項:
1. 重新審視自動化流程(5A tv_collect 連 TV API 失敗 11 天)
2. 00403A 歷史補抓(5/12 ~ 5/30 共 19 天)
3. ETF 範圍 review 機制(每年 1 月汰弱換強)

用戶決策:**5A 自動化「先不修,留給上線後重做」**,開發階段用 5/19 + 5/20 既有資料 demo。

---

## Demo 使用方式

### 啟動本地 server
```bash
cd ~/台股儀表板
python3 -m http.server 8080
```

### 主 demo URL
```
http://127.0.0.1:8080/docs/index_v2.html
```

### 切換到 5/19(B 級國巨 + 59 筆 ETF events)
```bash
python3 src/run_filters_v2.py     --date 2026-05-19 --output filtered_result_v2.json
python3 src/prepare_charts_v2.py  --date 2026-05-19 --result filtered_result_v2.json
python3 src/render_v2.py          --date 2026-05-19
```

### 切換到 5/20(S 級長榮 baseline)
```bash
python3 src/run_filters_v2.py     --date 2026-05-20 --output filtered_result_v2.json
python3 src/prepare_charts_v2.py  --date 2026-05-20 --result filtered_result_v2.json
python3 src/render_v2.py          --date 2026-05-20
```

兩天的 chart JSON 並存於 `docs/data/v2/2026-05-19/` 和 `docs/data/v2/2026-05-20/`,
切換只重 render HTML 即可。

---

## 重要 context(下次接續看這個)

| 項目 | 值 |
|---|---|
| ETF 6 檔精選池 | 00981A / 00987A / 00992A / 00994A / 00995A / 00403A |
| 共識窗口期 | **7 日累計**(規則文件 §1-A 已寫) |
| ⛔ ETF 減碼純標籤窗口 | **當天**(例外,個股卡即時提醒) |
| `chip_etf` parity | 100%(5 天驗證,W2.3) |
| 系統定位 | **找買點的雷達 + ETF 籌碼面雙向掃描** |
| 開發信條 | 先文件後程式(MACD 落差後建立) |
| K 線資料來源 | TradingView Desktop CDP(5B,目前停在 5/20) |
| ETF 動作資料來源 | etfedge.xyz → `~/ETF追蹤/etf_operations.db`(5A,5/20 仍新鮮) |
| 全部測試 | **350 tests / 0.064s / OK** |

---

*整理:Claude Code(2026-06-01,W3 demo 結案後)*
*下一階段:Polish #2/#3/#5 + Watchlist v2 + git commit feat/stage8-w3*
