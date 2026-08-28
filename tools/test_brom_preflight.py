#!/usr/bin/env python3
"""BROM permission preflight and transport diagnostics (host-only)."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _installer():
    spec = importlib.util.spec_from_file_location(
        "libreecho_install_preflight", ROOT / "tools" / "libreecho-install.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = _installer()


def fake_node(name: str):
    return pathlib.Path("/dev") / name


class PermissionPreflightTests(unittest.TestCase):
    def test_no_nodes_is_quiet(self) -> None:
        with mock.patch.object(INSTALLER, "_tty_nodes", return_value=[]):
            INSTALLER.brom_permission_preflight()

    def test_non_media_tek_node_is_ignored(self) -> None:
        node = fake_node("ttyACM0")
        with mock.patch.object(INSTALLER, "_tty_nodes", return_value=[node]), \
             mock.patch.object(INSTALLER, "_tty_is_media_tek", return_value=False), \
             mock.patch.object(os, "open", side_effect=PermissionError) as opener:
            INSTALLER.brom_permission_preflight()
        opener.assert_not_called()

    def test_blocked_media_tek_node_raises_actionable_error(self) -> None:
        node = fake_node("ttyACM0")
        with mock.patch.object(INSTALLER, "_tty_nodes", return_value=[node]), \
             mock.patch.object(INSTALLER, "_tty_is_media_tek", return_value=True), \
             mock.patch.object(os, "geteuid", return_value=1000), \
             mock.patch.object(os, "open", side_effect=PermissionError):
            with self.assertRaises(INSTALLER.InstallerError) as caught:
                INSTALLER.brom_permission_preflight()
        message = str(caught.exception)
        self.assertIn("ttyACM0", message)
        self.assertIn("permission denied", message)
        self.assertIn("usermod -aG dialout", message)
        self.assertIn("sudo python3", message)

    def test_openable_media_tek_node_passes(self) -> None:
        node = fake_node("ttyACM0")
        with mock.patch.object(INSTALLER, "_tty_nodes", return_value=[node]), \
             mock.patch.object(INSTALLER, "_tty_is_media_tek", return_value=True), \
             mock.patch.object(os, "geteuid", return_value=1000), \
             mock.patch.object(os, "open", return_value=7) as opener, \
             mock.patch.object(os, "close") as closer:
            INSTALLER.brom_permission_preflight()
        closer.assert_called_once_with(7)
        self.assertEqual(opener.call_args.args[0], node)

    def test_root_skips_preflight(self) -> None:
        with mock.patch.object(os, "geteuid", return_value=0), \
             mock.patch.object(INSTALLER, "_tty_nodes", side_effect=AssertionError("must not scan")):
            INSTALLER.brom_permission_preflight()

    def test_busy_node_notes_and_continues(self) -> None:
        node = fake_node("ttyACM0")
        with mock.patch.object(INSTALLER, "_tty_nodes", return_value=[node]), \
             mock.patch.object(INSTALLER, "_tty_is_media_tek", return_value=True), \
             mock.patch.object(os, "geteuid", return_value=1000), \
             mock.patch.object(os, "open", side_effect=OSError("Device or resource busy")):
            INSTALLER.brom_permission_preflight()


class TransportDiagnosticsTests(unittest.TestCase):
    def test_diagnostics_never_raise(self) -> None:
        with mock.patch.object(pathlib.Path, "glob", side_effect=OSError("no sysfs")):
            INSTALLER._print_brom_transport_diagnostics()

    def test_diagnostics_classify_known_identities(self) -> None:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), \
             mock.patch.object(INSTALLER, "_tty_nodes", return_value=[]), \
             mock.patch.object(INSTALLER, "_running_process_names", return_value=set()), \
             mock.patch.object(INSTALLER.pathlib.Path, "glob", return_value=iter([])):
            INSTALLER._print_brom_transport_diagnostics()
        self.assertIn("BROM transport diagnostics", buffer.getvalue())
        self.assertIn("no MediaTek USB device", buffer.getvalue())

    def test_healthy_brom_diagnostics_do_not_show_recovery_warning(self) -> None:
        import contextlib
        import io

        output = io.StringIO()
        with contextlib.redirect_stdout(output), \
             mock.patch.object(INSTALLER, "_media_tek_usb_devices", return_value={"5-2.3": ("0e8d", "0003")}), \
             mock.patch.object(INSTALLER, "_tty_nodes", return_value=[Path("/dev/ttyACM0")]), \
             mock.patch.object(INSTALLER, "_tty_is_media_tek", return_value=True), \
             mock.patch.object(INSTALLER, "_running_process_names", return_value={"ModemManager"}):
            INSTALLER._print_brom_transport_diagnostics()
        text = output.getvalue()
        self.assertIn("BROM transport is healthy; no transport action is needed.", text)
        self.assertNotIn("ModemManager is running", text)
        self.assertNotIn("remedy if nothing helps", text)

    def test_diagnostics_do_not_run_again_after_amonet_progress(self) -> None:
        source = (ROOT / "tools/libreecho-install.py").read_text(encoding="utf-8")
        self.assertIn(
            'last_state == "Waiting for BROM/USB..." and now - last_notice >= 30',
            source,
        )

class ProgressLoopIntegrationTests(unittest.TestCase):
    def test_preflight_runs_before_prompt(self) -> None:
        calls = []

        def fake_preflight():
            calls.append("preflight")
            raise INSTALLER.InstallerError("blocked")

        with mock.patch.object(INSTALLER, "brom_permission_preflight", side_effect=fake_preflight):
            with self.assertRaises(INSTALLER.InstallerError):
                INSTALLER.run_amonet_with_progress(
                    pathlib.Path("/nonexistent/launcher.sh"), pathlib.Path("/tmp"), 1
                )
        self.assertEqual(calls, ["preflight"])

    def test_stall_diagnostics_emitted_after_30s_without_progress(self) -> None:
        import io
        import contextlib

        calls = []

        class FakePopen:
            def __init__(self, argv, cwd=None):
                pass

            def poll(self):
                return None

            def kill(self):
                pass

            def wait(self):
                return 0

        def fake_diag():
            calls.append("diag")

        clock = iter([t for t in range(0, 500)])

        def fake_monotonic():
            return next(clock)

        def fake_sleep(_seconds):
            return None

        log_dir = pathlib.Path(self_tmp := __import__("tempfile").mkdtemp())
        (log_dir / "modules").mkdir()
        with mock.patch.object(INSTALLER.subprocess, "Popen", FakePopen), \
             mock.patch.object(INSTALLER.time, "monotonic", fake_monotonic), \
             mock.patch.object(INSTALLER.time, "sleep", fake_sleep), \
             mock.patch.object(INSTALLER, "_print_brom_transport_diagnostics", side_effect=fake_diag), \
             mock.patch.object(INSTALLER, "brom_permission_preflight"), \
             mock.patch("builtins.print"):
            with self.assertRaises(INSTALLER.InstallerError):
                INSTALLER.run_amonet_with_progress(
                    pathlib.Path("launcher.sh"), log_dir, 300
                )
        self.assertIn("diag", calls)
        import shutil

        shutil.rmtree(self_tmp)


class SidecarConsistencyTests(unittest.TestCase):
    def test_installer_exposes_preflight_symbols(self) -> None:
        source = (ROOT / "tools" / "libreecho-install.py").read_text(encoding="utf-8")
        self.assertIn("def brom_permission_preflight", source)
        self.assertIn("brom_permission_preflight()", source)
        self.assertIn("def _print_brom_transport_diagnostics", source)
        self.assertIn("usermod -aG dialout", source)


if __name__ == "__main__":
    unittest.main()
