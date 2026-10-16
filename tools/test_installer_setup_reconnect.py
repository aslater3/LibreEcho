"""Regression tests: the installer keeps first-run setup reachable across device reboots.

The adb forward lives on the host and is dropped whenever the Echo reboots
(for example after accepting device Wi-Fi firmware during setup). The
installer must notice, restore the forward for the same serial, and reopen
the setup page rather than leaving the owner with a dead localhost URL.
"""
from __future__ import annotations

import contextlib
import http.server
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("libreecho_install_reconnect", ROOT / "tools/libreecho-install.py")
assert SPEC and SPEC.loader
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)

SERIAL = "TESTSERIAL01"
PORT = 18080
RELEASE = "radar-puffin-v0.13.20"
BUNDLE = "a" * 64


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def run_supervisor(states, online_states, *, forward_ok=True, **kwargs):
    """Drive supervise_setup with scripted probe/online results."""
    states = list(states)
    online_states = list(online_states)
    clock = FakeClock()
    calls = {"forward": [], "browser": [], "online": 0}

    def probe(port):
        assert port == PORT
        return states.pop(0) if states else states_default

    states_default = kwargs.pop("default_state", None)

    def online(adb_bin, serial):
        assert serial == SERIAL
        calls["online"] += 1
        return online_states.pop(0) if online_states else True

    def forward(adb_bin, serial, port):
        calls["forward"].append((serial, port))
        return forward_ok

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = INSTALLER.supervise_setup(
            "adb", SERIAL, PORT, True,
            probe=probe, online=online, forward=forward,
            browser=calls["browser"].append, sleep=clock.sleep, clock=clock,
            **kwargs,
        )
    return result, calls, out.getvalue()


class SuperviseSetupTests(unittest.TestCase):
    def test_reboot_restores_forward_and_reopens_setup(self) -> None:
        # setup open -> device reboots (unreachable, adb offline x2) -> adb back,
        # forward restored -> setup reachable -> setup completed.
        result, calls, out = run_supervisor(
            [False, None, None, None, False, True],
            [False, False, True],
        )
        self.assertEqual(result, "completed")
        self.assertEqual(calls["forward"], [(SERIAL, PORT)])
        self.assertEqual(calls["browser"], [f"http://127.0.0.1:{PORT}/setup.html"])
        self.assertIn("device is rebooting", out)
        self.assertIn("setup page reconnected", out)

    def test_forward_dropped_without_reboot_is_restored_silently(self) -> None:
        result, calls, _ = run_supervisor([False, None, False, True], [True])
        self.assertEqual(result, "completed")
        self.assertEqual(calls["forward"], [(SERIAL, PORT)])
        self.assertEqual(calls["browser"], [], "page stays open when the device did not reboot")

    def test_already_completed_returns_immediately(self) -> None:
        result, calls, _ = run_supervisor([True], [])
        self.assertEqual(result, "completed")
        self.assertEqual(calls["forward"], [])
        self.assertEqual(calls["online"], 0)

    def test_device_that_never_returns_fails_with_recovery_command(self) -> None:
        with self.assertRaises(INSTALLER.InstallerError) as raised:
            run_supervisor([False], [], default_state=None, reconnect_timeout=30, poll_interval=3)
        message = str(raised.exception)
        self.assertIn(f"adb -s {SERIAL} wait-for-device", message)
        self.assertIn(f"adb -s {SERIAL} forward tcp:{PORT} tcp:8080", message)

    def test_overall_limit_prints_reconnect_hint(self) -> None:
        result, _, out = run_supervisor([False], [], default_state=False, limit=30, poll_interval=3)
        self.assertEqual(result, "timed-out")
        self.assertIn(f"forward tcp:{PORT} tcp:8080", out)

    def test_ctrl_c_detaches_without_touching_the_device(self) -> None:
        def probe(port):
            raise KeyboardInterrupt

        forward = mock.Mock()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = INSTALLER.supervise_setup("adb", SERIAL, PORT, False, probe=probe,
                                               forward=forward, sleep=lambda _s: None)
        self.assertEqual(result, "detached")
        forward.assert_not_called()
        self.assertIn(f"adb -s {SERIAL} wait-for-device", out.getvalue())


class ProbeSetupStateTests(unittest.TestCase):
    def serve(self, body: bytes, status: int = 200):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def test_reads_setup_completed_flag(self) -> None:
        for flag in (False, True):
            port = self.serve(json.dumps({"data": {"setup_completed": flag}, "error": None}).encode())
            self.assertIs(INSTALLER.probe_setup_state(port), flag)

    def test_unreachable_or_malformed_is_none(self) -> None:
        self.assertIsNone(INSTALLER.probe_setup_state(self.serve(b"not json")))
        self.assertIsNone(INSTALLER.probe_setup_state(self.serve(b'{"data":{}}')))
        self.assertIsNone(INSTALLER.probe_setup_state(self.serve(b"{}", status=503)))


class ReconnectActionTests(unittest.TestCase):
    def state_root(self, phase: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        INSTALLER._write_state(INSTALLER._state_path(directory, "default"),
                               {"phase": phase, "release": RELEASE, "bundle_sha256": BUNDLE,
                                "device_serial": SERIAL})
        return directory

    def test_reconnect_restores_forward_for_saved_serial(self) -> None:
        root = self.state_root("WEBUI_FORWARDED")
        with mock.patch.object(INSTALLER, "wait_for_transport") as wait, \
                mock.patch.object(INSTALLER, "select_adb_serial", return_value=SERIAL) as select, \
                mock.patch.object(INSTALLER, "_run_command") as run, \
                mock.patch.object(INSTALLER.webbrowser, "open") as browser, \
                contextlib.redirect_stdout(io.StringIO()):
            result = INSTALLER.reconnect_setup(state_root=root, install_id="default",
                                               wait_for_setup=False)
        wait.assert_called_once_with(["adb", "-s", SERIAL, "get-state"], "device", 180, "ADB")
        select.assert_called_once_with("adb", SERIAL)
        run.assert_called_once_with(["adb", "-s", SERIAL, "forward", f"tcp:{PORT}", "tcp:8080"], 20)
        browser.assert_called_once_with(f"http://127.0.0.1:{PORT}/setup.html")
        self.assertEqual(result["url"], f"http://127.0.0.1:{PORT}/setup.html")

    def test_reconnect_refuses_unfinished_install(self) -> None:
        root = self.state_root("FEATURES_STAGED")
        with mock.patch.object(INSTALLER, "_run_command") as run, \
                self.assertRaisesRegex(INSTALLER.InstallerError, "continue-one-shot"):
            INSTALLER.reconnect_setup(state_root=root, install_id="default")
        run.assert_not_called()

    def test_reconnect_without_saved_install(self) -> None:
        directory = Path(tempfile.mkdtemp())
        with self.assertRaisesRegex(INSTALLER.InstallerError, "no saved installation"):
            INSTALLER.reconnect_setup(state_root=directory, install_id="default")


class WiringTests(unittest.TestCase):
    source = (ROOT / "tools/libreecho-install.py").read_text(encoding="utf-8")

    def test_cli_exposes_reconnect_and_opt_out(self) -> None:
        self.assertIn('"continue-one-shot", "reconnect"))', self.source)
        self.assertIn('"--no-wait-for-setup"', self.source)
        self.assertEqual(self.source.count("wait_for_setup=not args.no_wait_for_setup,"), 3)

    def test_forward_completion_supervises_setup(self) -> None:
        self.assertGreaterEqual(
            self.source.count('result["setup"] = supervise_setup(adb_bin, serial, local_port, open_browser)'), 2)


if __name__ == "__main__":
    unittest.main()
