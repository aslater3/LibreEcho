#!/usr/bin/env python3
"""Bounded bulk-upload regressions for Product issue #176; no device access."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

INSTALLER = Path(__file__).resolve().parent / "libreecho-install.py"


class FeatureUploadTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location("installer_upload_test", INSTALLER)
        assert spec is not None and spec.loader is not None
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.cache = Path(self.temporary.name)
        release = "radar-puffin-v0.13.18"
        bundle = self.cache / release / "bundle"
        bundle.mkdir(parents=True)
        records = {}
        for kind, name, data in (
            ("payload", "tts.squashfs", b"synthetic upload fixture"),
            ("manifest", "tts.manifest.json", b"{}\n"),
        ):
            (bundle / name).write_bytes(data)
            records[kind] = {"name": name, "size": len(data),
                             "sha256": hashlib.sha256(data).hexdigest()}
        self.manifest = {"release": release, "features": [{"name": "tts", **records}]}

    def stage(self, timeout: float = 180) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.installer.stage_device_features(
                "adb", "synthetic-device", self.cache, self.manifest, timeout
            )

    def check_budget(self, requested: float, expected: float) -> None:
        result = subprocess.CompletedProcess([], 0, "FEATURE_STAGE_OK:tts\n", "")
        with mock.patch.object(self.installer, "_run_command", return_value=result) as control, \
             mock.patch.object(self.installer, "_run_command_with_heartbeat", return_value=result) as upload, \
             mock.patch.object(self.installer, "verify_device_features") as verify:
            self.stage(requested)
        upload.assert_called_once()
        argv, budget, message = upload.call_args.args
        self.assertEqual(argv[0:4], ["adb", "-s", "synthetic-device", "push"])
        self.assertEqual(argv[-1], "/tmp/libreecho-tts.squashfs")
        self.assertEqual(budget, expected)
        self.assertIn("tts", message)
        self.assertTrue(control.call_args_list)
        for call in control.call_args_list:
            self.assertEqual(call.args[1], requested)
            self.assertNotIn("flash", call.args[0])
            self.assertNotIn("reboot", call.args[0])
        verify.assert_called_once_with(
            "adb", "synthetic-device", {"features": self.manifest["features"]}, requested
        )

    def test_bulk_upload_gets_900_seconds_without_extending_control_commands(self) -> None:
        self.check_budget(180, 900)

    def test_larger_operator_timeout_is_preserved(self) -> None:
        self.check_budget(1200, 1200)

    def test_failed_upload_cannot_stage_or_verify_a_feature(self) -> None:
        result = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(self.installer, "_run_command", return_value=result) as control, \
             mock.patch.object(self.installer, "_run_command_with_heartbeat", \
                               side_effect=self.installer.InstallerError("upload timed out")), \
             mock.patch.object(self.installer, "verify_device_features") as verify:
            with self.assertRaisesRegex(self.installer.InstallerError, "upload timed out"):
                self.stage()
        # Only the helper script is pushed before the payload. No manifest,
        # configuration, root staging operation or verification follows failure.
        self.assertEqual(control.call_count, 1)
        self.assertEqual(control.call_args.args[0][-1], "/tmp/libreecho-stage-feature-root.sh")
        verify.assert_not_called()

    def test_upload_deadline_kills_process_and_retains_partial_output(self) -> None:
        process = mock.Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["adb", "push"], 15),
            ("partial transfer progress\n", "transport stalled\n"),
        ]
        with mock.patch.object(self.installer.subprocess, "Popen", return_value=process), \
             mock.patch.object(self.installer.time, "monotonic", side_effect=[0, 0, 15, 901]), \
             mock.patch.object(self.installer, "_append_log") as log, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(self.installer.InstallerError, "timed out after 900s"):
                self.installer._run_command_with_heartbeat(["adb", "push"], 900, "Uploading")
        process.kill.assert_called_once_with()
        self.assertEqual(process.communicate.call_args_list, [mock.call(timeout=15), mock.call()])
        messages = "\n".join(call.args[0] for call in log.call_args_list)
        self.assertIn("COMMAND timeout after 900s", messages)
        self.assertIn("partial transfer progress", messages)
        self.assertIn("transport stalled", messages)

    def test_normal_adb_failure_is_not_misreported_as_timeout(self) -> None:
        process = mock.Mock(returncode=1)
        process.communicate.return_value = ("", "error: device offline")
        with mock.patch.object(self.installer.subprocess, "Popen", return_value=process), \
             mock.patch.object(self.installer.time, "monotonic", side_effect=[0, 0]):
            with self.assertRaisesRegex(self.installer.InstallerError, r"command failed \(1\).*device offline"):
                self.installer._run_command_with_heartbeat(["adb", "push"], 900, "Uploading")
        process.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
