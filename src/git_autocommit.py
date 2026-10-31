"""
git_autocommit.py — 手動資料工具完成後,自動提交「自己改的檔案」(2026-10-31)。

背景:publish.sh 原本 `git add src/ config/ ...` 一網打盡,主跑當下工作樹裡的未完成改動
會被自動提交推上線。改為 publish 只提交 docs/ 發佈產物;config 這類資料型設定由
「改它的工具」在完成當下自己提交(convert_key_prices --splice、add_symbols_batch --apply),
附明確 commit message,git 歷史即版本備份。

規則:
  - 只提交指定 paths(`git commit -- paths`),不帶入其他已 staged 的東西。
  - 任一 path 不在 repo 內(如測試用 tmp 檔)→ 整筆略過,不提交。
  - 不 push(下一次主跑 publish.sh 的 git push 會一起推)。
  - 失敗只印警告、回 None,不讓工具本身失敗(檔案已寫好,提交是附加動作)。
"""
from __future__ import annotations
import os
import subprocess
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def short_codes(symbols, limit: int = 10) -> str:
    """['TWSE:3034','NASDAQ:ARM'] → '3034/ARM';超過 limit 檔 → 前 limit 檔 + '…'。"""
    codes = [s.split(":", 1)[-1] for s in symbols]
    head = "/".join(codes[:limit])
    return head + ("…" if len(codes) > limit else "")


def commit_files(paths, message: str, *, repo: str = PROJECT_ROOT, _run=subprocess.run) -> str | None:
    """提交 paths 並回 commit hash;無變更 / 不在 repo 內 / git 失敗 → None(印原因)。"""
    repo = os.path.realpath(repo)
    rel = []
    for p in paths:
        ap = os.path.realpath(p)
        if os.path.commonpath([ap, repo]) != repo:
            return None                          # repo 外(測試 tmp 等):靜默略過
        rel.append(os.path.relpath(ap, repo))
    try:
        _run(["git", "add", "--", *rel], cwd=repo, check=True, capture_output=True, text=True)
        diff = _run(["git", "diff", "--cached", "--quiet", "--", *rel], cwd=repo)
        if diff.returncode == 0:
            print("[git] 指定檔無變更,未提交")
            return None
        _run(["git", "commit", "-q", "-m", message, "--", *rel],
             cwd=repo, check=True, capture_output=True, text=True)
        h = _run(["git", "rev-parse", "--short", "HEAD"], cwd=repo,
                 check=True, capture_output=True, text=True).stdout.strip()
        print(f"[git] ✅ 已提交 {h}:{message.splitlines()[0]}(未 push,下次主跑一起推)")
        return h
    except (subprocess.CalledProcessError, OSError) as e:
        err = getattr(e, "stderr", "") or str(e)
        print(f"[git] ⚠️ 自動提交失敗(檔案已寫入,請手動提交):{str(err).strip()[:200]}",
              file=sys.stderr)
        return None
