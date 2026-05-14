# 台股動能作戰儀表板

**每日掃描 87 檔台股，透過五階段過濾 + ETF 籌碼追蹤，自動產出可互動的 HTML 儀表板。**

🌐 [線上預覽](https://mardichao-dotcom.github.io/daily-stock-analysis/)

---

## 這是什麼

這是一套完全本機運算、資料不依賴第三方 API 的台股動能篩選系統。

每天收盤後，系統自動：
1. 從本機 SQLite 讀取 87 檔台股的 K 線與 ETF 籌碼資料
2. 跑五階段過濾邏輯，將個股分為 S / A / 中性 / 警報 / 黑名單 五個等級
3. 產出帶互動 K 線圖的 HTML 儀表板，並累積歷史存檔
4. 部署到 GitHub Pages，可從任何裝置查看

---

## 技術架構

```
本機 SQLite                  Python 過濾引擎              靜態網站
─────────────────            ────────────────────          ──────────────────
kline.db                ──▶  stage1: ETF籌碼            ──▶ docs/index.html
(87 檔 K 線)                  stage2: 族群長子                (Jinja2 渲染)
                              stage3: 國際領先指標
etf_operations.db       ──▶  stage4: 積分計算
(ETF 每日增減倉)              stage5: 分級輸出
```

**前端**：純靜態 HTML + [TradingView Lightweight Charts v5](https://tradingview.github.io/lightweight-charts/)，零伺服器、零框架。

**後端**：Python 3.9+，依賴只有 `jinja2`。所有計算邏輯在本機跑，GitHub Pages 只 host 產出物。

---

## 篩選邏輯簡介

### 五階段過濾

| 階段 | 名稱 | 核心判斷 |
|------|------|---------|
| 1 | ETF 籌碼 | ≥2 檔 ETF 共識加碼、連續加碼、異常點火建倉 |
| 2 | 族群長子 | 板塊「領頭羊」是否發動（漲停/爆量+漲>3%/跳空） |
| 3 | 國際領先指標 | 對應美股/日股族群的表現是否同步或背離 |
| 4 | 積分計算 | 正向訊號 +，負向訊號 −，最終積分排名 |
| 5 | 分級輸出 | S / A / 中性 / 警報 / 黑名單 |

### 積分制（v1）

- **1-A（+3）** 自身是族群長子且發動
- **1-B（+2）** ETF 共識加碼（≥2 檔）
- **1-C（+2）** 突破 60 日高
- **1-D（+1）** 跳空開高
- **1-E（+2）** 大爆量（量比 >2x）
- **2-A（+1）** ETF 連續加碼（雙軌）
- **2-B（+1）** 強紅 K（實體比 >60%）
- **2-C（−2）** 族群長子大跌（>3%，未啟動時）
- **2-E（−1）** 多長子背離
- **2-F（−1）** ETF 經理人分歧

閾值：S 級 ≥ 7，A 級 ≥ 4。

---

## 儀表板功能

- **10 個區塊**：S/A/中性/警報/黑名單/ETF分歧/單兵作戰/國際指標/全球比較/T+N追蹤
- **折疊式互動 K 線圖**（按需載入，不影響頁面效能）
  - ETF 箭頭分層：大亮 = 共識事件（≥2 檔），小暗 = 單一 ETF
  - ETF 勾選框過濾（顯示濾鏡，不影響共識歷史事實）
  - Hover 明細：日期、動作、各 ETF 買賣張數
  - 近 1 個月 / 近 3 個月時間切換
- **歷史存檔**：每日儀表板永久保留，可瀏覽任一歷史日期

---

## 本機執行

### 環境需求

```bash
python3 --version   # 3.9+
pip3 install jinja2
```

### 一條龍產出

```bash
bash run_all.sh
# 產出到 docs/，可用瀏覽器直接開 docs/index.html
```

### 單步執行

```bash
# 步驟 1：五階段過濾
python3 src/run_filters.py

# 步驟 2：產出圖表 JSON（S/A 級個股 K 線 + ETF markers）
python3 src/prepare_charts.py

# 步驟 3：渲染 HTML
python3 src/render.py

# 步驟 4：更新歷史存檔索引
python3 src/generate_index.py
```

### 本機預覽（含圖表）

```bash
# 必須用 HTTP server，不能直接開檔案（fetch API 限制）
python3 -m http.server 8080
# 開啟 http://localhost:8080/docs/index.html
```

---

## 資料說明

- **K 線資料**：本機 `kline.db`（透過 TradingView 每日抓取，階段 5 接入）
- **ETF 籌碼**：本機 `etf_operations.db`（87 檔股票的所有 ETF 持倉增減記錄）
- **資料庫不在此 repo**：GitHub Pages 只 host 產出的 HTML/JSON/JS，資料庫留本機

---

## 專案結構

```
.
├── src/                    # 核心 Python 腳本
│   ├── run_filters.py      # 五階段過濾主程式
│   ├── filter_stage*.py    # 各階段過濾邏輯
│   ├── prepare_charts.py   # 圖表 JSON 產生
│   ├── render.py           # Jinja2 HTML 渲染
│   ├── generate_index.py   # 歷史存檔索引
│   ├── load_config.py      # 股票名單/板塊讀取
│   ├── load_data.py        # SQLite 資料讀取
│   └── score.py            # 積分計算
├── config/
│   └── watchlist.json      # 87 檔股票清單（名稱/板塊/長子）
├── templates/
│   ├── dashboard.html.j2   # Jinja2 主模板
│   └── archive_index.html.j2
├── docs/                   # GitHub Pages 根目錄
│   ├── index.html          # 最新儀表板
│   ├── assets/             # JS/CSS（LW Charts v5 + 自製圖表模組）
│   ├── data/               # 圖表 K 線 JSON（依日期分資料夾）
│   └── archives/           # 歷史存檔
├── run_all.sh              # 一條龍腳本
├── publish.sh              # 部署到 GitHub Pages
└── 積分制定案_v1_正式版.md  # 積分系統設計文件
```

---

## 部署

```bash
bash publish.sh
# 約 1-2 分鐘後 GitHub Pages 更新
```

---

## 開發階段

| 階段 | 內容 | 狀態 |
|------|------|------|
| 1 | 五階段過濾引擎 | ✅ 完成 |
| 2 | 互動 K 線圖系統（LW Charts v5） | ✅ 完成 |
| 3 | HTML 模板化（Jinja2）+ 一條龍腳本 | ✅ 完成 |
| 4 | GitHub Pages 部署 | ✅ 完成 |
| 5 | cron 自動化 + Discord 通知 | 🔜 規劃中 |
| 6 | 關鍵價標記功能 | 🔜 規劃中 |

---

*資料僅供個人研究參考，不構成投資建議。*
