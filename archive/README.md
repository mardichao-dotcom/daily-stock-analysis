# archive/ — 封存(非刪除)

> 封存日期:2026-09-18
> 方式:`git mv`(保留 git 歷史,可完整還原)
> 原因:從 7 個 launchd 排程 + 主腳本往下追依賴,以下檔案追不到任何活的進入點引用,
>       且經實跑驗證封存後 v2 主鏈/週報鏈/手動工具全部正常。

---

## archive/v1/ — v1 舊管線(13 檔,已停產)

v1 管線在 2026-06 已從 run_all.sh 移除(見 docs/上線後待辦.md §0),由 v2 完全取代:
- run_filters.py → 被 run_filters_v2.py 取代
- score.py / filter_stage1.py / filter_stage2.py / filter_stage4.py → v1 分階段計分
- render.py → 被 render_v2.py 取代
- render_watchlist.py → 被 render_watchlist_v2.py 取代
- prepare_charts.py → 被 prepare_charts_v2.py 取代
- generate_index.py → v1 首頁
- classify.py → v1 分類
- load_data.py → v1 資料載入(etf_io.py 已獨立重實作,不依賴它)
- key_price_state.py → v1 關鍵價狀態(v2 內建於 run_filters_v2)
- load_key_prices.py → v1 讀 .txt 關鍵價(v2 讀 config/key_prices.json)

這 13 檔互相 import、自成孤島,只被彼此和 stage*_spec.md 文件提及。
封存前確認:無任何活的進入點 import(render_landing 的 render() 是自己 def 的,
不是 v1 render;run_filters_v2/prepare_charts_v2/etf_io 對 v1 的引用都是「註解」不是 import)。

## archive/tools/ — 一次性除錯工具 + v1 相依工具(3 檔)

- diagnose_tv_collect.mjs → TV 採集除錯,無任何引用
- probe_new_api.mjs → TV API 探測,無任何引用
- compare_v1_v2.py → v1↔v2 計分比對工具。v1 已封存 = 它必然失敗;留在 tools/
  會讓人誤以為可用,故一併封存。要用時需連同 v1 一起還原
  (`git mv archive/v1/*.py src/` + `git mv archive/tools/compare_v1_v2.py tools/`)。

---

## archive/md-layer/ — key_prices 中間層 md(1 主檔 + 5 備份,已名存實亡)

- key_prices_clean_v3.md → 曾是關鍵價的「人可讀中間層」,轉換器從它產 JSON。
  但已名存實亡:只 112 檔(config/key_prices.json 有 140+)、32 檔手改股與 JSON 分岔、
  表達能力不足。2026-09-15 上架 79 檔走「直接對 JSON 逐檔替換」完全沒經過它,
  證明不需要它也能更新。
- key_prices_clean_v3.md.bak* (5 個)→ 歷次上架的 md 備份。

配套改動(封存 md 時一併做,見 tools/convert_key_prices.py):
- 移除 DEFAULT_MD,`--md` 改必填 → 無參數重跑不會再去讀(已封存的)那個 md。
- 上架流程改為:判讀批 → 生成該批小 md → `convert --md 該批.md --out 片段.json`
  → 拼接進 config/key_prices.json。config/key_prices.json 是唯一權威真值。
- **防重跑保護保留**(--out 檔數 > 本次產出就中止):它保護的是 JSON 目標,
  與 md 是否封存無關;任何時候用小批 md 跑都可能誤覆蓋 140 檔真值,這道防線仍需要。

## 還原方法

還原單一檔:
```
git mv archive/v1/run_filters.py src/run_filters.py
```

還原整個 v1:
```
git mv archive/v1/*.py src/
git mv archive/tools/*.mjs scripts/
```

git 歷史完整保留,`git log --follow archive/v1/run_filters.py` 可看封存前的所有變更。

---

## 已知副作用(封存後)

- 無殘留的失效工具:`compare_v1_v2.py` 已一併封存至 archive/tools/(2026-09-18),
  tools/ 底下不再有依賴 v1 的檔案。
