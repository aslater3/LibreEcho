#!/usr/bin/env python3
"""Host userdata-image dependency preflight tests."""
from __future__ import annotations

import importlib.util
import os
import struct
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_installer():
    spec = importlib.util.spec_from_file_location(
        "libreecho_install_host_preflight", ROOT / "tools/libreecho-install.py"
    )
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


def sparse_header(expected_bytes: int) -> bytes:
    blocks = expected_bytes // 4096
    return struct.pack(
        "<IHHHHIIII",
        0xED26FF3A,
        1,
        0,
        28,
        12,
        4096,
        blocks,
        1,
        0,
    ) + struct.pack("<HHII", 0xCAC3, 0, blocks, 12)


class HostFastbootPreflightTests(unittest.TestCase):
    def test_failure_evidence_archives_available_fastboot_and_adb(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root / "bin"
            bindir.mkdir()
            for name, body in {
                "fastboot": "#!/bin/sh\nif [ \"$1\" = devices ]; then printf 'FB123 fastboot\\n'; fi\n",
                "adb": "#!/bin/sh\nif [ \"$1\" = devices ]; then printf 'List of devices attached\\nADB123\\tdevice product:test\\n'; fi\n",
                "lsusb": "#!/bin/sh\nprintf 'Bus 001 Device 001: ID 0e8d:0003 MediaTek\\n'\n",
            }.items():
                command = bindir / name
                command.write_text(body, encoding="ascii")
                command.chmod(0o755)
            log = root / "run.log"
            log.write_text("original failure\n", encoding="utf-8")
            args = type("Args", (), {
                "fastboot_bin": "fastboot", "adb_bin": "adb",
                "fastboot_serial": "auto", "cache_root": root / "cache",
            })()
            with mock.patch.object(INSTALLER, "ACTIVE_LOG_PATH", log), \
                 mock.patch.dict(os.environ, {"PATH": f"{bindir}:{os.environ['PATH']}"}):
                archive = INSTALLER.collect_failure_evidence(args, "simulated failure")
            self.assertIsNotNone(archive)
            assert archive is not None
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            with tarfile.open(archive, "r:gz") as stream:
                names = stream.getnames()
            for expected in (
                "failure.txt", "libreecho-installer.log", "fastboot-devices.txt",
                "fastboot-FB123-getvar-all.txt", "adb-devices.txt", "adb-ADB123-props.txt",
            ):
                self.assertTrue(any(name.endswith(expected) for name in names), expected)

    def test_stages_complete_userdata_toolset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fastboot = fake_executable(root, "fastboot")
            fake_executable(root, "mke2fs")
            img2simg = fake_executable(root, "img2simg")
            with mock.patch.object(INSTALLER, "_find_img2simg", return_value=img2simg):
                staged = Path(
                    INSTALLER.prepare_fastboot_tools(str(fastboot), root / "cache")
                )
            self.assertEqual(staged, root / "cache" / "host-tools" / "fastboot")
            for name in ("fastboot", "mke2fs", "img2simg"):
                helper = staged.with_name(name)
                self.assertTrue(helper.is_file())
                self.assertTrue(helper.stat().st_mode & 0o111)
            result = subprocess.run(
                [str(staged), "--version"], capture_output=True, text=True, check=False
            )
            self.assertEqual(result.returncode, 0)

    def test_userdata_builds_compatible_sparse_then_flashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fastboot = fake_executable(root, "fastboot")
            fake_executable(root, "mke2fs")
            fake_executable(root, "img2simg")
            commands: list[list[str]] = []
            timeouts: list[float] = []

            def fake_run(argv, timeout, *args, check=True):
                command = list(argv)
                commands.append(command)
                timeouts.append(timeout)
                if Path(command[0]).name == "dumpe2fs":
                    return subprocess.CompletedProcess(
                        command, 0, "Free blocks: 1-267135\n", ""
                    )
                if Path(command[0]).name == "img2simg":
                    Path(command[-1]).write_bytes(sparse_header(INSTALLER.USERDATA_BYTES))
                return subprocess.CompletedProcess(command, 0, "", "")

            with mock.patch.object(INSTALLER, "verify_fastboot_product"), \
                 mock.patch.object(
                     INSTALLER,
                     "_fastboot_partition_size",
                     return_value=INSTALLER.USERDATA_BYTES,
                 ), \
                 mock.patch.object(INSTALLER, "_run_command", side_effect=fake_run), \
                 mock.patch.object(
                     INSTALLER, "_run_command_with_heartbeat", side_effect=fake_run
                 ):
                INSTALLER.format_userdata_in_fastboot(str(fastboot), "SERIAL", 120)

            mke2fs = next(command for command in commands if Path(command[0]).name == "mke2fs")
            self.assertIn("^64bit,^metadata_csum,^metadata_csum_seed,^orphan_file", mke2fs)
            img2simg = next(command for command in commands if Path(command[0]).name == "img2simg")
            self.assertEqual(img2simg[1], "-s")
            self.assertFalse(any("format:ext4" in command for command in commands))
            flash = commands[-1]
            self.assertEqual(flash[:5], [str(fastboot), "-s", "SERIAL", "flash", "userdata"])
            self.assertTrue(flash[5].endswith("userdata.sparse.img"))
            self.assertEqual(timeouts[-1], INSTALLER.USERDATA_FLASH_TIMEOUT)

    def test_long_command_prints_elapsed_heartbeat(self) -> None:
        with mock.patch("builtins.print") as output:
            result = INSTALLER._run_command_with_heartbeat(
                ["/bin/sh", "-c", "sleep 0.08"],
                1,
                "still working",
                interval=0.02,
            )
        self.assertEqual(result.returncode, 0)
        messages = [str(call.args[0]) for call in output.call_args_list]
        self.assertTrue(any("still working" in message and "elapsed" in message for message in messages))

    def test_sparse_validator_rejects_full_partition_fill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "slow.sparse.img"
            blocks = INSTALLER.USERDATA_BYTES // 4096
            header = struct.pack(
                "<IHHHHIIII", 0xED26FF3A, 1, 0, 28, 12, 4096, blocks, 1, 0
            )
            image.write_bytes(header + struct.pack("<HHII", 0xCAC2, 0, blocks, 16) + b"\0" * 4)
            with self.assertRaisesRegex(INSTALLER.InstallerError, "program too much data"):
                INSTALLER._validate_android_sparse_image(image, INSTALLER.USERDATA_BYTES)

    def test_sparse_header_validation_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "bad.img"
            image.write_bytes(b"not an android sparse image")
            with self.assertRaises(INSTALLER.InstallerError):
                INSTALLER._validate_android_sparse_image(image, INSTALLER.USERDATA_BYTES)

    def test_missing_format_tool_fails_before_device_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fastboot = fake_executable(root, "fastboot")
            with mock.patch.object(INSTALLER, "_find_mke2fs", return_value=None), \
                 mock.patch.object(INSTALLER, "_find_img2simg", return_value=None), \
                 mock.patch.object(INSTALLER, "_install_host_format_tools") as install:
                with self.assertRaisesRegex(INSTALLER.InstallerError, "--install-host-deps"):
                    INSTALLER.prepare_fastboot_tools(str(fastboot), root / "cache")
            install.assert_not_called()

    def test_missing_dependency_install_is_opt_in(self) -> None:
        source = (ROOT / "tools/libreecho-install.py").read_text(encoding="utf-8")
        self.assertIn("--install-host-deps", source)
        self.assertIn("android-sdk-libsparse-utils", source)
        result = subprocess.run(
            ["python3", str(ROOT / "tools/libreecho-install.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("--install-host-deps", result.stdout)


if __name__ == "__main__":
    unittest.main()
