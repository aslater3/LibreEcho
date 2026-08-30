#!/usr/bin/env python3
"""Ensure the public Product installer mirror is complete and immutable."""
from __future__ import annotations

import hashlib
import re
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

    def test_run_one_shot_wrapper_is_packaged_in_complete_release(self) -> None:
        source = (ROOT / "build/ci/prepare-dev-release.py").read_text(encoding="utf-8")
        self.assertIn("run-one-shot.sh", source)

    def test_tools_readme_documents_self_download_and_full_flow(self) -> None:
        readme = (ROOT / "tools" / "README.md").read_text(encoding="utf-8")
        for marker in (
            "release checksum inventory",
            "download and verify pinned Amonet",
            "--release-tag \"$TAG\"",
            "./run-one-shot.sh latest",
            "--execute-hardware",
            "initial-install.tar",
            "stage and verify all five feature payloads",
        ):
            self.assertIn(marker, readme)

    def test_run_one_shot_bootstrap_is_shell_and_checksum_gated(self) -> None:
        wrapper = ROOT / "tools" / "run-one-shot.sh"
        self.assertTrue(wrapper.is_file())
        self.assertFalse(wrapper.is_symlink())
        source = wrapper.read_text(encoding="utf-8")
        self.assertIn("SHA256SUMS", source)
        self.assertIn("sha256sum -c", source)
        self.assertIn("exec python3", source)
        self.assertIn("--release-tag \"$TAG\"", source)
        self.assertIn("latest", source)
        self.assertIn("releases/tags/latest", source)
        self.assertIn("--release-dir \"$work\"", source)

    def test_install_guide_uses_copyable_public_wrapper_syntax(self) -> None:
        guide = (ROOT / "docs/install/README.md").read_text(encoding="utf-8")
        code_blocks = re.findall(r"```(?:sh|bash)\n(.*?)\n```", guide, re.S)
        self.assertTrue(code_blocks)
        for block in code_blocks:
            for line in block.splitlines():
                self.assertNotRegex(line, r"\\\\\\s*$",
                                     f"doubled shell continuation: {line!r}")
        self.assertIn("./run-one-shot.sh latest", guide)
        self.assertNotIn("gh release list", guide)
        self.assertIn("public GitHub API/download URLs", guide)

    def test_continuation_validates_the_requested_release_tag(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("state[\"release\"] != release_tag", source)
        self.assertIn("continuation release tag does not match saved state", source)
        self.assertTrue(source.startswith("#!/usr/bin/env python3\n"))
        self.assertNotIn("/home/andy", source)
        self.assertNotIn("/media/andy", source)
        self.assertNotIn("PRIVATE", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
