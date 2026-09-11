#!/usr/bin/env python3
"""Behavioral checks for the GitHub-backed component-cache boundary."""
from __future__ import annotations
import os
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


class WakeDependencyClosureTests(unittest.TestCase):
    """Run the shipped staging loop: cached ORT must remain linkable."""

    def stage(self, source: Path, destination: Path):
        builder = (ROOT / "build/build.sh").read_text()
        start = builder.index("      for dep_archive in ")
        end = builder.index("      absl_count=0", start)
        return subprocess.run(
            ["bash", "-euc", builder[start:end]],
            env={**os.environ, "WAKE_ORT_BUILD": str(source),
                 "WAKE_ORT_WORK": str(destination)},
            capture_output=True, text=True, timeout=10,
        )

    def fixture(self, source: Path):
        for name in ("onnx-build/libonnx.a", "onnx-build/libonnx_proto.a",
                     "nsync-build/libnsync_cpp.a", "protobuf-build/libprotobuf-lite.a",
                     "flatbuffers-build/libflatbuffers.a"):
            path = source / "_deps" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"archive fixture")

    def test_cached_nsync_archive_can_be_linked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, cached = root / "source", root / "cached"
            self.fixture(source)
            archive = source / "_deps/nsync-build/libnsync_cpp.a"
            archive.unlink()
            (root / "sync.c").write_text("int sync_fixture(void) { return 0; }\n")
            (root / "main.c").write_text(
                "int sync_fixture(void); int main(void) { return sync_fixture(); }\n")
            subprocess.run(["cc", "-c", str(root / "sync.c"), "-o", str(root / "sync.o")],
                           check=True, timeout=10)
            subprocess.run(["ar", "rcs", str(archive), str(root / "sync.o")],
                           check=True, timeout=10)
            result = self.stage(source, cached)
            self.assertEqual(result.returncode, 0, result.stderr)
            restored = cached / "_deps/nsync-build/libnsync_cpp.a"
            self.assertTrue(restored.is_file(), "nsync lost from reduced ORT cache closure")
            self.assertEqual(restored.read_bytes(), archive.read_bytes())
            subprocess.run(["cc", str(root / "main.c"), str(restored), "-o", str(root / "probe")],
                           check=True, timeout=10)
            subprocess.run([str(root / "probe")], check=True, timeout=10)

    def test_missing_or_symlinked_nsync_is_not_cached(self):
        for symlink in (False, True):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source = root / "source"
                self.fixture(source)
                archive = source / "_deps/nsync-build/libnsync_cpp.a"
                archive.unlink()
                if symlink:
                    archive.symlink_to(source / "_deps/onnx-build/libonnx.a")
                result = self.stage(source, root / "cached")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("nsync-build/libnsync_cpp.a", result.stderr)

if __name__ == "__main__":
    unittest.main()
