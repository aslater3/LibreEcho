"""LibreEcho #257: stock-size userdata, honest /data diagnosis, confirmed reboot, --reinstall."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "tools/libreecho-install.py"
WRAPPER = ROOT / "tools/run-one-shot.sh"
RELEASE_OLD = "radar-puffin-v0.14.0"
RELEASE_NEW = "radar-puffin-v0.14.1"


def load():
    spec = importlib.util.spec_from_file_location("installer_257", INSTALLER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def done(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, stdout, "")


class StagerTests(unittest.TestCase):
    def test_stager_never_mounts_userdata_itself(self) -> None:
        installer = load()
        stager = installer.ROOT_FEATURE_STAGER
        self.assertNotIn("mount -t ext4", stager)
        self.assertNotIn("/dev/mmcblk0p16", stager)
        self.assertIn("FEATURE_STAGE_DATA_NOT_MOUNTED", stager)

    def test_stager_refuses_when_data_is_not_a_mount(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            busybox = root / "bb"
            busybox.write_text(
                "#!/bin/sh\ncase \"$1\" in grep) exit 1 ;; *) shift; exec \"$@\" ;; esac\n", encoding="utf-8")
            busybox.chmod(0o755)
            config = root / "conf"
            payload = root / "p"
            manifest = root / "m"
            payload.write_bytes(b"x")
            manifest.write_bytes(b"y")
            config.write_text(
                f"FEATURE_ID=tts\nPAYLOAD_SHA256={'0' * 64}\nPAYLOAD_SIZE=1\nPAYLOAD_FILE={payload}\n"
                f"MANIFEST_FILE={manifest}\nMANIFEST_SHA256={'0' * 64}\nMANIFEST_SIZE=1\n", encoding="utf-8")
            script = load().ROOT_FEATURE_STAGER.replace("BB=/bin/busybox", f"BB={busybox}").replace(
                "/tmp/libreecho-feature-stage.conf", str(config))
            result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FEATURE_STAGE_DATA_NOT_MOUNTED", result.stdout)


class DataMountDiagnosisTests(unittest.TestCase):
    def test_unmounted_data_is_reported_instead_of_hash_mismatch(self) -> None:
        installer = load()

        def run(command, timeout, check=True):
            if command[-1].startswith("if grep -q ' /data '"):
                return done("DATA_UNMOUNTED\nlibreecho-init: userdata-size-unsupported\nlibreecho-init: userdata-identity-rejected\n")
            return done("sha256sum: /data/libreecho/features/airplay2/payload.squashfs: No such file\n", rc=1)

        manifest = {"features": [{"name": "airplay2", "payload": {"sha256": "0" * 64},
                                  "manifest": {"sha256": "1" * 64}}]}
        with mock.patch.object(installer, "_run_command", side_effect=run):
            with self.assertRaises(installer.InstallerError) as raised:
                installer.verify_device_features("adb", "SERIAL", manifest)
        message = str(raised.exception)
        self.assertIn("did not mount userdata", message)
        self.assertIn("userdata-size-unsupported", message)
        self.assertIn("--reinstall", message)
        self.assertNotIn("hash mismatch", message)

    def test_real_mismatch_on_mounted_data_is_still_a_hash_mismatch(self) -> None:
        installer = load()

        def run(command, timeout, check=True):
            if command[-1].startswith("if grep -q ' /data '"):
                return done("DATA_MOUNTED\n")
            return done(f"{'b' * 64}  {command[-1]}\n")

        manifest = {"features": [{"name": "tts", "payload": {"sha256": "a" * 64}, "manifest": {"sha256": "a" * 64}}]}
        with mock.patch.object(installer, "_run_command", side_effect=run):
            with self.assertRaisesRegex(installer.InstallerError, "tts installed payload hash mismatch"):
                installer.verify_device_features("adb", "SERIAL", manifest)

    def test_matching_readback_needs_no_extra_probe(self) -> None:
        installer = load()
        digest = "a" * 64
        calls: list[list[str]] = []

        def run(command, timeout, check=True):
            calls.append(command)
            return done(f"{digest}  {command[-1]}\n")

        manifest = {"features": [{"name": "tts", "payload": {"sha256": digest}, "manifest": {"sha256": digest}}]}
        with mock.patch.object(installer, "_run_command", side_effect=run):
            installer.verify_device_features("adb", "SERIAL", manifest)
        self.assertEqual(len(calls), 2)


class ConfirmedRebootTests(unittest.TestCase):
    def test_file_exists_output_is_accepted_only_when_the_boot_changes(self) -> None:
        installer = load()
        boot_ids = iter(["boot-1", "boot-2"])

        def run(command, timeout, check=True):
            if command[-1] == "reboot":
                return done("reboot failed: File exists\n")
            return done("device\n")

        with mock.patch.object(installer, "_run_command", side_effect=run), \
             mock.patch.object(installer, "_adb_shell_text", side_effect=lambda *a: next(boot_ids)), \
             mock.patch.object(installer.time, "sleep"), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            installer.adb_reboot_confirmed("adb", "SERIAL", timeout=30)
        self.assertIn("File exists", out.getvalue())

    def test_transport_drop_counts_as_reboot(self) -> None:
        installer = load()

        def run(command, timeout, check=True):
            if command[-1] == "reboot":
                return done("")
            return done("", rc=1)

        with mock.patch.object(installer, "_run_command", side_effect=run), \
             mock.patch.object(installer, "_adb_shell_text", return_value="boot-1"), \
             mock.patch.object(installer.time, "sleep"):
            installer.adb_reboot_confirmed("adb", "SERIAL", timeout=30)

    def test_reboot_that_never_happens_fails_instead_of_polling_startup(self) -> None:
        installer = load()
        clock = iter(range(0, 1000, 5))

        def run(command, timeout, check=True):
            if command[-1] == "reboot":
                return done("reboot failed: File exists\n")
            return done("device\n")

        with mock.patch.object(installer, "_run_command", side_effect=run), \
             mock.patch.object(installer, "_adb_shell_text", return_value="boot-1"), \
             mock.patch.object(installer.time, "sleep"), \
             mock.patch.object(installer.time, "monotonic", side_effect=lambda: next(clock)), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(installer.InstallerError, "did not reboot"):
                installer.adb_reboot_confirmed("adb", "SERIAL", timeout=30)

    def test_final_stage_uses_confirmed_reboot(self) -> None:
        source = INSTALLER_PATH.read_text(encoding="utf-8")
        start = source.index("def finish_install(")
        body = source[start:source.index("\ndef ", start + 1)]
        self.assertNotIn('"reboot"]', body)
        self.assertIn("adb_reboot_confirmed(adb_bin, serial)", body)


class ReinstallConfirmationTests(unittest.TestCase):
    def test_requires_typed_erase_or_yes(self) -> None:
        installer = load()
        with contextlib.redirect_stdout(io.StringIO()):
            installer.confirm_reinstall(True, ask=lambda _: "", interactive=False)
            installer.confirm_reinstall(False, ask=lambda _: "ERASE", interactive=True)
            with self.assertRaisesRegex(installer.InstallerError, "cancelled"):
                installer.confirm_reinstall(False, ask=lambda _: "yes", interactive=True)
            with self.assertRaisesRegex(installer.InstallerError, "--yes"):
                installer.confirm_reinstall(False, ask=lambda _: "ERASE", interactive=False)

    def test_cli_rejects_reinstall_outside_one_shot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                ["python3", str(INSTALLER_PATH), "continue-one-shot", "--release-tag", RELEASE_NEW,
                 "--reinstall", "--yes", "--state-root", temporary, "--cache-root", temporary,
                 "--log-file", str(Path(temporary) / "log")],
                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--reinstall is only valid with one-shot", result.stderr)


class OneShotReinstallTests(unittest.TestCase):
    """Drive the real one_shot() with a stub transport from a #257-style saved state."""

    def run_one_shot(self, *, saved_phase: str | None, reinstall: bool, unlocked: bool = True,
                     device_in_fastboot: bool = False, saved_serial: str = "SERIAL123",
                     fastboot_serial: str = "auto") -> dict:
        installer = load()
        events: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache_root, state_root = root / "cache", root / "state"
            release_dir = root / "release"
            release_dir.mkdir()
            bundle = release_dir / "bundle.tar"
            bundle.write_bytes(b"bundle")
            boot = cache_root / RELEASE_NEW / "bundle" / "boot.img"
            boot.parent.mkdir(parents=True)
            boot.write_bytes(b"boot")
            state_file = state_root / "default" / "state.json"
            if saved_phase is not None:
                state_file.parent.mkdir(parents=True)
                state_file.write_text(json.dumps({
                    "phase": saved_phase, "release": RELEASE_OLD,
                    "bundle_sha256": hashlib.sha256(b"old").hexdigest(),
                    "board": "radar_puffin", "device_serial": saved_serial, "userdata_formatted": True,
                }), encoding="utf-8")
            manifest = {"release": RELEASE_NEW, "boot": {"name": "boot.img", "sha256": "0" * 64}, "features": []}
            fastboot = {"present": device_in_fastboot}

            def adb_reboot_bootloader(command, timeout, check=True):
                if command[-2:] == ["reboot", "bootloader"]:
                    events.append("adb-reboot-bootloader")
                    fastboot["present"] = True
                return done("")

            installer._prepare = lambda *a, **k: (manifest, bundle)
            installer.validate_public_boot_image = lambda *a, **k: None
            installer.require_host_commands = lambda *a, **k: None
            installer.prepare_fastboot_tools = lambda binary, *a, **k: binary
            installer.adb_forward_command = lambda *a, **k: ["adb", "forward"]
            installer.fastboot_devices = lambda binary: ["SERIAL123"] if fastboot["present"] else []
            installer.fastboot_devices_safe = installer.fastboot_devices
            installer.select_adb_serial = lambda binary, requested: "SERIAL123"
            installer.verify_fastboot_product = lambda *a, **k: "radar_puffin"
            installer.fastboot_getvar = lambda b, s, name: ("true" if unlocked else "false") if name == "unlock_status" else ""
            installer.identify_kaeru = lambda *a, **k: None
            installer.extract_amonet_payload = lambda *a, **k: root / "payload"
            installer.select_amonet_payload = lambda *a, **k: {}
            installer.brick_fastboot_payload = lambda *a, **k: events.append("BRICK")
            installer.claim_brick_latch = lambda *a, **k: events.append("latch")
            installer.format_userdata_in_fastboot = lambda *a, **k: events.append("format-userdata")
            installer._verify_fastboot_payload_geometry = lambda *a, **k: "pinned"
            installer.reboot_to_installed_system = lambda *a, **k: events.append("reboot-system")
            installer.wait_for_first_boot_adb = lambda *a, **k: None
            installer.collect_adb_diagnostics = lambda *a, **k: None
            installer.verify_adb_payload_readback = lambda *a, **k: None
            installer.stage_device_features = lambda *a, **k: events.append("stage")
            installer._complete_install = lambda *a, **k: {"phase": "DONE"}
            installer._run_command = adb_reboot_bootloader

            def flash(command, timeout, check=True):
                if "flash" in command:
                    events.append("flash:" + command[-2])
                return adb_reboot_bootloader(command, timeout, check)

            installer._run_command = flash
            error = None
            result = None
            with contextlib.redirect_stdout(io.StringIO()) as out:
                try:
                    result = installer.one_shot(
                        release_dir, root / "amonet.zip" if not unlocked else None,
                        cache_root=cache_root, state_root=state_root, release_tag=RELEASE_NEW,
                        fastboot_bin="fastboot", adb_bin="adb", fastboot_serial=fastboot_serial,
                        fastboot_timeout=5, adb_timeout=5, open_browser=False, execute_hardware=True,
                        target="radar_puffin", reinstall=reinstall,
                    )
                except installer.InstallerError as exc:
                    error = str(exc)
            archives = sorted(p.name for p in state_file.parent.glob("state.json.before-reinstall-*")) \
                if state_file.parent.exists() else []
            current = json.loads(state_file.read_text()) if state_file.exists() else None
        return {"events": events, "error": error, "result": result, "archives": archives,
                "state": current, "out": out.getvalue()}

    def test_plain_rerun_over_a_failed_install_still_refuses_and_names_reinstall(self) -> None:
        run = self.run_one_shot(saved_phase="FEATURES_STAGED", reinstall=False, device_in_fastboot=True)
        self.assertIn("--reinstall", run["error"])
        self.assertEqual(run["events"], [])
        self.assertEqual(run["state"]["release"], RELEASE_OLD, "saved state untouched")

    def test_reinstall_from_running_device_reaches_fastboot_and_reinstalls_without_brick(self) -> None:
        run = self.run_one_shot(saved_phase="FEATURES_STAGED", reinstall=True, device_in_fastboot=False)
        self.assertIsNone(run["error"])
        self.assertEqual(run["result"], {"phase": "DONE"})
        events = run["events"]
        self.assertEqual(events[0], "adb-reboot-bootloader")
        self.assertNotIn("BRICK", events)
        self.assertNotIn("latch", events)
        self.assertLess(events.index("format-userdata"), events.index("flash:boot_a"))
        self.assertIn("flash:boot_b", events)
        self.assertEqual(len(run["archives"]), 1, "old state archived, not deleted")
        self.assertEqual(run["state"]["release"], RELEASE_NEW)

    def test_reinstall_from_fastboot_does_not_reboot_first(self) -> None:
        run = self.run_one_shot(saved_phase="WEBUI_FORWARDED", reinstall=True, device_in_fastboot=True)
        self.assertIsNone(run["error"])
        self.assertNotIn("adb-reboot-bootloader", run["events"])
        self.assertIn("format-userdata", run["events"])

    def test_reinstall_never_discards_an_unresolved_brick_marker(self) -> None:
        for phase in ("BRICK_SUBMITTED", "AMONET_VERIFIED"):
            with self.subTest(phase=phase):
                run = self.run_one_shot(saved_phase=phase, reinstall=True, device_in_fastboot=True)
                self.assertIn("unresolved brick marker", run["error"])
                self.assertEqual(run["events"], [])
                self.assertEqual(run["archives"], [])

    def test_reinstall_never_sends_the_brick_to_a_locked_unit(self) -> None:
        run = self.run_one_shot(saved_phase="FEATURES_STAGED", reinstall=True, unlocked=False,
                                device_in_fastboot=True)
        self.assertIn("never sends the brick", run["error"])
        self.assertNotIn("BRICK", run["events"])
        self.assertNotIn("latch", run["events"])
        self.assertNotIn("format-userdata", run["events"])

    def test_reinstall_refuses_another_device_bound_to_the_install_id(self) -> None:
        run = self.run_one_shot(saved_phase="FEATURES_STAGED", reinstall=True, device_in_fastboot=True,
                                saved_serial="OTHER", fastboot_serial="SERIAL123")
        self.assertIn("belongs to another device", run["error"])
        self.assertEqual(run["events"], [])
        self.assertEqual(run["archives"], [])


class WrapperTests(unittest.TestCase):
    def test_wrapper_reads_target_from_saved_state_for_reinstall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state" / "default"
            state.mkdir(parents=True)
            (state / "state.json").write_text(json.dumps({"board": "radar_puffin"}), encoding="utf-8")
            fake_bin = root / "bin"
            fake_bin.mkdir()
            for name in ("fastboot", "adb"):
                tool = fake_bin / name
                tool.write_text("#!/bin/sh\necho unexpected-$0 \"$@\" >&2\nexit 1\n", encoding="utf-8")
                tool.chmod(0o755)
            curl = fake_bin / "curl"
            curl.write_text("#!/bin/sh\necho stop-before-download >&2\nexit 9\n", encoding="utf-8")
            curl.chmod(0o755)
            env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "TMPDIR": temporary}
            result = subprocess.run(
                ["bash", str(WRAPPER), RELEASE_NEW, "--reinstall", "--state-root", str(root / "state")],
                capture_output=True, text=True, timeout=30, env=env)
        self.assertIn("Target from saved installer state: radar_puffin", result.stdout)
        self.assertIn("stop-before-download", result.stderr)
        self.assertNotIn("unexpected-", result.stderr)

    def test_wrapper_rejects_reinstall_with_continue(self) -> None:
        result = subprocess.run(["bash", str(WRAPPER), RELEASE_NEW, "--continue", "--reinstall"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--reinstall is a one-shot option", result.stderr)


if __name__ == "__main__":
    unittest.main()
