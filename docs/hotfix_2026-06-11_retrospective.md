# hotfix 2026-06-11 回顧 — 黑箱診斷 vs 實際根因

> 整理日期:2026-06-12
> 範圍:hotfix_2026-06-11(P0 美股/國際股回歸修復 + P1 體檢清理)
> 用途:這次最有交接價值的教訓——任務書的根因是「從線上 JSON 反推的黑箱診斷」,
>       三處 P0 的**症狀都對、歸因都錯**。記下來,避免未來照著錯歸因動刀。

---

## 核心教訓:症狀在哪一層 ≠ 為什麼

黑箱診斷(curl 線上 JSON / HTML 反推)很擅長定位「**症狀在哪一層**」,
但「**為什麼**」必須回本機 `git log -p` + 讀 code + 查 DB 驗證。
這次三處都是「線上看到的症狀正確,但反推的成因錯誤」。任務書那句
**「動工前先在本機驗證假設,與實際 code 不符時以實際為準」**直接救了修復方向——
若照黑箱歸因動刀,三處都會修錯地方。

---

## 三處差異

### P0-A 美股 chart 停產(404)
| | |
|---|---|
| 黑箱假設 | (a) 新鮮度檢查 `last_bar==data_date`;**(b) 6/7~6/8 擴張 commit 改了 market/exchange 分支** |
| 實際根因 | 假設 (a) **對**;假設 (b) **錯** |
| 證據 | `git log --since=2026-06-06 -- src/`:擴張 commit `692b0a3` 在 src/ 只改 `add_symbols_batch.py` 一行 regex,**沒碰 chart 管線**。真正的閘是 `prepare_charts_v2.py:203` 的 `kline[-1]["time"] != date`,來自更早的 `ba4ac04`(6/2)。美股 19:00 台北跑時資料永遠晚一個交易日 → 全停產 |
| 教訓 | 「擴張後才壞 → 一定是擴張造成的」是很自然的時間相關性誤推。git log 證明 chart 管線根本沒被擴張碰過 |

### P0-B 國際股關鍵價遺失(lines=0)
| | |
|---|---|
| 黑箱假設 | 擴張為支援 KRX 改了 symbol 正規化 / lookup key → 非台股查找 miss;**且擴張前國際股正常(6/5 NVDA=5 條)** |
| 實際根因 | 沒有 lookup 在斷裂;國際股**從未被 `run_filters_v2` 計分**(它只跑 `iter_tw_symbols`)→ 從來沒有 `key_prices_snapshot` → chart 層給空 lines |
| 證據 | `filtered_result_v2.json` 只有 TWSE+TPEX=98 檔、零國際股;`key_prices_snapshot` 自 v2 元始 commit `54baef6` 就是唯一來源。6/5 NVDA 的 5 條線是 **6/8 一次性手補** commit `f8d2379`(force 回填 718 個歷史 chart JSON)寫進去的,不是管線產出 |
| 教訓 | 「線上某天看起來正常」可能是**人工補丁的殘留**,不能當「回歸前基準」。修法也因此不同:不是修 lookup,是 chart 層改 fallback 直接讀 config/key_prices.json |

### P0-C 未收盤半成品 K 棒污染
| | |
|---|---|
| 黑箱假設 | UTC→台北 +8 把美股 bar 日期**推後一天(時區 +1 偏移)** |
| 實際根因 | bar 日期用 `utcfromtimestamp`,與各交易所交易日對齊,**無 +1 偏移**;真正的污染是 `import_kline` 的 `INSERT OR IGNORE` + tv_collect「已是最新→skip」,讓收盤前抓的盤中半成品**永不被收盤值覆寫** |
| 證據 | 本機用收盤時刻邏輯(`exchange_hours.py`)+ `report_suspicious_bars.py` 掃 kline.db:6/11 19:12 唯一半成品是 **OMXCOP:MAERSK_B**(哥本哈根 23:00 才收),**美股 6/10 bar 已收、無誤判** |
| 教訓 | 同一現象(資料怪)有多個可能機制,別停在第一個合理假設。精準定位後發現是「歐股」不是「美股」、是「覆寫策略」不是「時區換算」 |

---

## 連帶價值

- 修法因為歸因正確而更穩:P0-B 改 chart 層 fallback(與 TW-only filter 解耦)永久解決,
  比修一個不存在的 lookup 健壯;P0-C 的 `REPLACE` + 重抓最近 N 根讓半成品**隔天自癒**。
- 一個 bug 串起多處:`TWSE:3665`(P0 無關)同時是 §6.1#2 ETF 空白名 + §6.2 6/10 略過的主角,
  根因都是「該檔當日 K 棒間歇缺漏」——查證時順手把三件事串起來。

---

## 一句話帶走

> 黑箱診斷給你「去哪裡找」,不給你「為什麼」。
> 每個「因為 X 所以壞」動刀前,回本機用 git log / code / DB 各驗一次——這次三次有三次歸因要修正。

---

*整理者:Claude Code(2026-06-12,hotfix 2026-06-11 結案後)*
