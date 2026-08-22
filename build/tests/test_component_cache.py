#!/usr/bin/env python3
"""Behavioral checks for the GitHub-backed component-cache boundary."""
from __future__ import annotations
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
CACHE = ROOT / "build/component-cache.py"

class ComponentCacheTests(unittest.TestCase):
    def key(self, tree: Path) -> str:
        return subprocess.check_output(
            ["python3", str(CACHE), "key", "--component", "smoke", "--tree", f"root={tree}"],
            text=True,
        ).strip()

    def test_external_symlink_is_hashed_logically_without_following_private_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tree"
            root.mkdir()
            (root / "payload").write_bytes(b"same")
            (root / "private-pointer").symlink_to("/external/private-state/CURRENT")
            first = self.key(root)
            (root / "private-pointer").unlink()
            (root / "private-pointer").symlink_to("/elsewhere/out/CURRENT")
            second = self.key(root)
            self.assertEqual(first, second)

    def test_same_content_in_different_directories_has_same_key(self):
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            a, b = Path(left) / "tree", Path(right) / "tree"
            for root in (a, b):
                (root / "nested").mkdir(parents=True)
                (root / "nested/item").write_bytes(b"stable")
            self.assertEqual(self.key(a), self.key(b))

if __name__ == "__main__":
    unittest.main()
