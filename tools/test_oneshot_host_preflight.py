#!/usr/bin/env python3
"""Host fastboot dependency preflight tests."""
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_installer():
    spec = importlib.util.spec_from_file_location("libreecho_install_host_preflight", ROOT / "tools/libreecho-install.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = load_installer()


class HostFastbootPreflightTests(unittest.TestCase):
    def test_stages_fastboot_with_mke2fs_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            staged = Path(INSTALLER.prepare_fastboot_tools("fastboot", Path(temporary)))
            self.assertEqual(staged, Path(temporary) / "host-tools" / "fastboot")
            helper = staged.with_name("mke2fs")
            self.assertTrue(staged.is_file())
            self.assertTrue(helper.is_file())
            self.assertTrue(staged.stat().st_mode & 0o111)
            self.assertTrue(helper.stat().st_mode & 0o111)
            result = subprocess.run([str(staged), "--version"], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0)

    def test_missing_mke2fs_fails_before_any_device_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
             mock.patch.object(INSTALLER, "_find_mke2fs", return_value=None), \
             mock.patch.object(INSTALLER, "_install_host_e2fsprogs") as install:
            with self.assertRaisesRegex(INSTALLER.InstallerError, "--install-host-deps"):
                INSTALLER.prepare_fastboot_tools("fastboot", Path(temporary))
        install.assert_not_called()

    def test_missing_dependency_install_is_opt_in(self) -> None:
        source = (ROOT / "tools/libreecho-install.py").read_text(encoding="utf-8")
        self.assertIn("--install-host-deps", source)
        self.assertIn("apt-get", source)
        result = subprocess.run(
            ["python3", str(ROOT / "tools/libreecho-install.py"), "--help"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--install-host-deps", result.stdout)


if __name__ == "__main__":
    unittest.main()
