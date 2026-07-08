"""
asset_version.py — 前端資產版本(cache-busting,stage10 Batch 1)

build hash = 全部前端資產內容的 md5 前 8 碼(內容驅動:資產一改 hash 即變,
瀏覽器/Pages CDN 舊快取自動失效)。六頁 head 統一經 head_snippet() 產出:
  - 主題 pre-paint script(深色預設,localStorage 記憶;放在 CSS 前避免閃白)
  - tokens.css → style_v2.css(順序固定:tokens 先定義變數)
  - theme.js(切換鈕)
verify_publish 以 _check_assets 斷言線上 HTML 引用的 ?v= 與線上資產內容一致。
"""
from __future__ import annotations
import hashlib
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ASSETS_DIR = os.path.join(PROJECT_ROOT, "docs", "assets")

# 參與 hash 的資產(引用時掛 ?v=;新增前端資產記得加進來)
VERSIONED_ASSETS = ["tokens.css", "style_v2.css", "theme.js", "chart_v2.js", "events.js",
                    "macro_dash.js"]


def build_hash(assets_dir: str = ASSETS_DIR) -> str:
    h = hashlib.md5()
    for name in VERSIONED_ASSETS:
        p = os.path.join(assets_dir, name)
        if os.path.exists(p):
            with open(p, "rb") as f:
                h.update(f.read())
    return h.hexdigest()[:8]


# 主題 pre-paint:必須 inline 且在 CSS link 之前(避免深→淺閃爍)
_THEME_BOOT = ("<script>try{document.documentElement.dataset.theme="
               "localStorage.getItem('theme')||'dark';}catch(e){"
               "document.documentElement.dataset.theme='dark';}</script>")


def head_snippet(prefix: str = "assets/") -> str:
    """六頁共用的 head 資產塊(含 cache-busting)。prefix 供子目錄頁面調整相對路徑。"""
    v = build_hash()
    return (f"{_THEME_BOOT}\n"
            f'  <link rel="stylesheet" href="{prefix}tokens.css?v={v}">\n'
            f'  <link rel="stylesheet" href="{prefix}style_v2.css?v={v}">\n'
            f'  <script src="{prefix}theme.js?v={v}" defer></script>')


def versioned(path: str) -> str:
    """單一資產加版:versioned('assets/chart_v2.js') → 'assets/chart_v2.js?v=xxxxxxxx'"""
    return f"{path}?v={build_hash()}"
