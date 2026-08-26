#!/usr/bin/env python3
"""Ensure the public Product installer mirror is complete and immutable."""
from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "tools" / "libreecho-install.py"
CHECKSUM = ROOT / "tools" / "libreecho-install.py.sha256"


class InstallerPublicationTests(unittest.TestCase):
    def test_checked_in_installer_matches_its_sha256_sidecar(self) -> None:
        self.assertTrue(INSTALLER.is_file())
        self.assertFalse(INSTALLER.is_symlink())
        self.assertTrue(CHECKSUM.is_file())
        self.assertFalse(CHECKSUM.is_symlink())
        expected = CHECKSUM.read_text(encoding="ascii").strip().split()
        self.assertEqual(len(expected), 2)
        self.assertEqual(expected[1], "libreecho-install.py")
        self.assertEqual(len(expected[0]), 64)
        self.assertEqual(expected[0], hashlib.sha256(INSTALLER.read_bytes()).hexdigest())

    def test_installer_accepts_build_release_checksum_inventory(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("radar-puffin-build-", source)
        self.assertIn("def download_release", source)
        self.assertIn("def download_amonet", source)
        self.assertIn("release_dir = download_release", source)

    def test_tools_readme_documents_self_download_and_full_flow(self) -> None:
        readme = (ROOT / "tools" / "README.md").read_text(encoding="utf-8")
        for marker in (
            "downloads the release assets itself",
            "download and verify pinned Amonet",
            "--release-tag \"$TAG\"",
            "--execute-hardware",
            "initial-install.tar",
            "stage and verify all five feature payloads",
        ):
            self.assertIn(marker, readme)

    def test_installer_is_python_source_with_no_private_path(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertTrue(source.startswith("#!/usr/bin/env python3\n"))
        self.assertNotIn("/home/andy", source)
        self.assertNotIn("/media/andy", source)
        self.assertNotIn("PRIVATE", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
