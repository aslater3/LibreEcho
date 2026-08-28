#!/usr/bin/env python3
"""Host fastboot dependency preflight tests."""
from __future__ import annotations

import importlib.util
import os
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


def fake_executable(root: Path, name: str) -> Path:
    path = root / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    path.chmod(0o755)
    return path


class HostFastbootPreflightTests(unittest.TestCase):
    def test_stages_fastboot_with_mke2fs_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fastboot = fake_executable(root, "fastboot")
            mke2fs = fake_executable(root, "mke2fs")
            staged = Path(INSTALLER.prepare_fastboot_tools(str(fastboot), root / "cache"))
            self.assertEqual(staged, root / "cache" / "host-tools" / "fastboot")
            helper = staged.with_name("mke2fs")
            self.assertTrue(staged.is_file())
            self.assertTrue(helper.is_file())
            self.assertTrue(staged.stat().st_mode & 0o111)
            self.assertTrue(helper.stat().st_mode & 0o111)
            conf = staged.with_name("mke2fs.conf")
            self.assertTrue(conf.is_file())
            conf_text = conf.read_text(encoding="ascii")
            self.assertIn("large_file", conf_text)
            self.assertNotIn("64bit", conf_text)
            self.assertNotIn("metadata_csum", conf_text)
            result = subprocess.run([str(staged), "--version"], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0)

    def test_format_userdata_pins_mke2fs_config_to_staged_conf(self) -> None:
        captured = {}

        def fake_run(argv, timeout, *, check=True):
            captured["argv"] = list(argv)
            captured["env_config"] = os.environ.get("MKE2FS_CONFIG")
            return subprocess.CompletedProcess(argv, 0, "", "")

        original = os.environ.get("MKE2FS_CONFIG")
        with mock.patch.object(INSTALLER, "verify_fastboot_product"), \
             mock.patch.object(INSTALLER, "_fastboot_partition_size", return_value=INSTALLER.USERDATA_BYTES), \
             mock.patch.object(INSTALLER, "_run_command", side_effect=fake_run):
            try:
                INSTALLER.format_userdata_in_fastboot(
                    "/tmp/fake-host-tools/fastboot", "SERIAL", 120
                )
            finally:
                if original is None:
                    os.environ.pop("MKE2FS_CONFIG", None)
                else:
                    os.environ["MKE2FS_CONFIG"] = original
        self.assertEqual(
            captured["argv"],
            ["/tmp/fake-host-tools/fastboot", "-s", "SERIAL", "format:ext4", "userdata"],
        )
        self.assertEqual(
            captured["env_config"], "/tmp/fake-host-tools/mke2fs.conf"
        )
        # restored after the operation
        self.assertEqual(os.environ.get("MKE2FS_CONFIG"), original)

    def test_missing_mke2fs_fails_before_any_device_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fastboot = fake_executable(root, "fastboot")
            with mock.patch.object(INSTALLER, "_find_mke2fs", return_value=None), \
                 mock.patch.object(INSTALLER, "_install_host_e2fsprogs") as install:
                with self.assertRaisesRegex(INSTALLER.InstallerError, "--install-host-deps"):
                    INSTALLER.prepare_fastboot_tools(str(fastboot), root / "cache")
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
