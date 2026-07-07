"""
test_asset_version.py — stage10 Batch1 cache-busting(內容驅動 hash)
"""
from __future__ import annotations
import os
import re
import sys
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from src import asset_version as av


class TestBuildHash(unittest.TestCase):
    def test_hash_is_8_hex(self):
        self.assertRegex(av.build_hash(), r"^[0-9a-f]{8}$")

    def test_content_change_changes_hash(self):
        with tempfile.TemporaryDirectory() as d:
            for n in av.VERSIONED_ASSETS:
                open(os.path.join(d, n), "w").write("x")
            h1 = av.build_hash(d)
            open(os.path.join(d, "style_v2.css"), "w").write("y")   # 改一個資產
            h2 = av.build_hash(d)
            self.assertNotEqual(h1, h2)

    def test_head_snippet_consistent_version(self):
        s = av.head_snippet()
        vs = set(re.findall(r"\?v=([0-9a-f]{8})", s))
        self.assertEqual(len(vs), 1)                    # tokens/style/theme 同一 v
        self.assertIn("tokens.css?v=", s)
        self.assertIn("style_v2.css?v=", s)
        self.assertIn("theme.js?v=", s)
        self.assertIn("dataset.theme", s)               # 深色 pre-paint
        self.assertLess(s.index("dataset.theme"), s.index("tokens.css"))  # script 在 CSS 前


if __name__ == "__main__":
    unittest.main()
