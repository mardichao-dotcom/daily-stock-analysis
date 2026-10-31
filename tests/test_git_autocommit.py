"""
test_git_autocommit.py — 2026-10-31:資料工具完成後只提交「自己改的檔案」。

publish.sh 改為只提交 docs/;config 由 convert_key_prices --splice / add_symbols_batch --apply
在完成當下自行提交。這裡驗:只提交指定檔、不帶入其他 staged、repo 外略過、無變更不提交;
以及 --splice 拼接(本批更新/新增,其餘保留)。
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src.git_autocommit import commit_files, short_codes


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True).stdout


class TestCommitFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@example.com")
        _git(self.repo, "config", "user.name", "t")
        for name in ("a.json", "b.json"):
            with open(os.path.join(self.repo, name), "w") as f:
                f.write("{}\n")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as f:
            f.write(text)

    def test_commits_only_given_path_not_other_staged(self):
        self._write("a.json", '{"x": 1}\n')
        self._write("b.json", '{"y": 2}\n')
        _git(self.repo, "add", "b.json")                      # 別人已 staged 的東西
        h = commit_files([os.path.join(self.repo, "a.json")], "關鍵價上架:測試 1 檔 2330",
                         repo=self.repo)
        self.assertTrue(h)
        self.assertEqual(_git(self.repo, "show", "--name-only", "--format=", "HEAD").split(),
                         ["a.json"])
        self.assertIn("b.json", _git(self.repo, "diff", "--cached", "--name-only"))  # 仍 staged、未被帶走
        self.assertEqual(_git(self.repo, "log", "-1", "--format=%s"), "關鍵價上架:測試 1 檔 2330\n")

    def test_no_change_returns_none(self):
        self.assertIsNone(commit_files([os.path.join(self.repo, "a.json")], "x", repo=self.repo))

    def test_path_outside_repo_skipped(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{}")
        try:
            self.assertIsNone(commit_files([f.name], "x", repo=self.repo))
        finally:
            os.unlink(f.name)
        self.assertEqual(_git(self.repo, "rev-list", "--count", "HEAD").strip(), "1")

    def test_short_codes(self):
        self.assertEqual(short_codes(["TWSE:3034", "TWSE:2379", "NASDAQ:ARM", "TWSE:5269"]),
                         "3034/2379/ARM/5269")
        self.assertTrue(short_codes([f"TWSE:{i}" for i in range(12)], limit=10).endswith("…"))


class TestSplice(unittest.TestCase):
    MD = ("### [TWSE:2330 台積電]\n線(實線):\n"
          "- 價格: 2500 | 顏色: 紅 | 文字: (無) | 形容詞: (無)\n---\n\n"
          "### [TWSE:9999 測試新增]\n線(實線):\n"
          "- 價格: 100 | 顏色: 黑 | 文字: (無) | 形容詞: (無)\n---\n")

    def test_splice_updates_and_adds_keeps_others(self):
        with open(os.path.join(PROJECT_ROOT, "config", "key_prices.json"), encoding="utf-8") as f:
            base = json.load(f)
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "kp.json")
            md = os.path.join(td, "batch.md")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(base, f, ensure_ascii=False, indent=2)
            with open(md, "w", encoding="utf-8") as f:
                f.write(self.MD)
            r = subprocess.run([sys.executable, "tools/convert_key_prices.py", "--md", md,
                                "--out", out, "--splice", "--label", "測試批"],
                               cwd=PROJECT_ROOT, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("更新 1 / 新增 1", r.stdout)
            self.assertNotIn("已提交", r.stdout)                 # repo 外 tmp → 不提交
            with open(out, encoding="utf-8") as f:
                got = json.load(f)
        self.assertEqual(len(got["stocks"]), len(base["stocks"]) + 1)    # 其餘保留
        self.assertEqual(got["stocks"]["TWSE:2330"]["lines"][0]["price"], "2500")
        self.assertIn("TWSE:9999", got["stocks"])
        self.assertIn("測試批 判讀拼接(更新1/新增1)", got["_note"])


if __name__ == "__main__":
    unittest.main()
