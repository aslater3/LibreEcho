"""Fail-closed gates around the fastbrick step, for both Radar and Biscuit.

Each test exercises the real installer functions. Only the fastboot and
subprocess boundaries are replaced, so every refusal is observed before any
write command is issued.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INSTALLER = load_module("libreecho_install_fastbrick", ROOT / "tools/libreecho-install.py")

RADAR_OLD = "59779ca-20220524_183401"
RADAR_NEW = "63cb91b-20221007_072309"
RADAR_NEW2 = "63cb91b-20221007_073612"
BISCUIT_LK = "63cb91b-20221007_072309"
TARGETS = {
    "radar_puffin": {"product": "RADAR", "lk_ok": (RADAR_OLD, RADAR_NEW, RADAR_NEW2)},
    "biscuit": {"product": "BISCUIT", "lk_ok": (BISCUIT_LK,)},
}
ARCHIVE_NAMES = {"radar_puffin": "amonet-radar-v1.0.0.zip", "biscuit": "amonet-biscuit-v2.0.0.zip"}
REAL_ARCHIVES = [Path.home() / "Downloads" / name for name in ARCHIVE_NAMES.values()]
ARCHIVE_ZIPS = {target: Path.home() / "Downloads" / name for target, name in ARCHIVE_NAMES.items()}


def completed(argv, rc=0, out="", err=""):
    return subprocess.CompletedProcess(argv, rc, out, err)


class FakeFastboot:
    """Scripted fastboot. Records every command so write-side absence is provable."""

    def __init__(self, product, *, unlocked=False, kaeru=None, lk="", kaeru_mode="stock"):
        self.product = product
        self.unlocked = unlocked
        self.lk = lk
        self.kaeru_mode = kaeru_mode  # "stock" | "kaeru" | "silent" | "mixed"
        self.calls: list[list[str]] = []
        self.serial_after_brick = None
        self.unlock_raw = None

    def run(self, argv, timeout, *, check=True):
        self.calls.append(list(argv))
        tail = argv[argv.index("getvar") + 1:] if "getvar" in argv else []
        if "getvar" in argv:
            name = tail[0]
            values = {"product": f"product: {self.product}\n",
                      "unlock_status": f"unlock_status: {self.unlock_raw if self.unlock_raw is not None else ('true' if self.unlocked else 'false')}\n",
                      "lk_build_desc": f"lk_build_desc: {self.lk}\n"}
            return completed(argv, 0, values.get(name, ""))
        if argv[-2:] == ["oem", "kaeru-version"]:
            if self.kaeru_mode == "kaeru":
                return completed(argv, 0, "INFOkaeru-v2\nOKAY\n")
            if self.kaeru_mode == "stock":
                return completed(argv, 1, "", "FAILunknown command\n")
            if self.kaeru_mode == "mixed":
                return completed(argv, 0, "OKAY\nFAILbad\n")
            return completed(argv, 0, "", "")  # silent: indeterminate
        return completed(argv, 0, "")


def make_archive(path: Path, members: dict[str, bytes], *, symlink: str | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, data in members.items():
            info = zipfile.ZipInfo(name)
            if symlink == name:
                info.external_attr = (0o120777) << 16  # symlink mode bits
            bundle.writestr(info, data)
    return path


class PinLayoutTests(unittest.TestCase):
    def test_every_pin_has_one_payload_per_reviewed_build_for_both_targets(self):
        for target, spec in TARGETS.items():
            with self.subTest(target=target):
                pin = INSTALLER.AMONET_PINS[target]
                self.assertEqual(pin["archive"], ARCHIVE_NAMES[target])
                self.assertEqual(set(pin["lk_builds"]), set(spec["lk_ok"]))
                for entry in pin["lk_builds"].values():
                    self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
                    self.assertGreater(entry["size"], 0)

    def test_radar_has_no_default_so_unreviewed_builds_are_refused(self):
        self.assertNotIn("default_payload", INSTALLER.AMONET_PINS["radar_puffin"])
        with self.assertRaisesRegex(INSTALLER.InstallerError, "no pinned fastbrick payload"):
            INSTALLER.select_amonet_payload("radar_puffin", "99999-unknown")

    def test_biscuit_accepts_any_build_through_its_pinned_default(self):
        for lk in (BISCUIT_LK, "anything-else-build", "00000-x"):
            with self.subTest(lk=lk):
                chosen = INSTALLER.select_amonet_payload("biscuit", lk)
                self.assertEqual(chosen["payload"], "fastbrick-20221007.img" if lk == BISCUIT_LK else "fastbrick.img")

    def test_empty_lk_build_is_refused_for_both_targets(self):
        for target in TARGETS:
            with self.subTest(target=target), self.assertRaisesRegex(INSTALLER.InstallerError, "LK build is unknown"):
                INSTALLER.select_amonet_payload(target, "")


class RealArchiveTests(unittest.TestCase):
    """Checks the committed pins against the real Amonet ZIPs when they are present."""

    def test_pins_match_real_archive_bytes(self):
        for target, path in zip(TARGETS, REAL_ARCHIVES):
            with self.subTest(target=target):
                if not path.exists():
                    self.skipTest(f"{path} not present in this checkout")
                pin = INSTALLER.AMONET_PINS[target]
                self.assertEqual(path.stat().st_size, pin["archive_size"])
                self.assertEqual(INSTALLER._sha256(path), pin["archive_sha256"])
                with zipfile.ZipFile(path) as bundle:
                    names = {info.filename: info.file_size for info in bundle.infolist()}
                    for entry in pin["lk_builds"].values():
                        self.assertEqual(names.get(f"amonet/bin/{entry['payload']}"), entry["size"])

    def test_extraction_reproduces_each_pinned_payload_hash(self):
        for target, path in zip(TARGETS, REAL_ARCHIVES):
            if not path.exists():
                self.skipTest(f"{path} not present in this checkout")
            for lk, entry in INSTALLER.AMONET_PINS[target]["lk_builds"].items():
                with self.subTest(target=target, lk=lk), tempfile.TemporaryDirectory() as tmp:
                    out = INSTALLER.extract_amonet_payload(path, target, entry, Path(tmp))
                    self.assertEqual(INSTALLER._sha256(out), entry["sha256"])


class KaeruGateTests(unittest.TestCase):
    def plan(self, fake, target="radar_puffin", amonet=None):
        cache = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(cache, ignore_errors=True))
        with mock.patch.object(INSTALLER, "_run_command", side_effect=fake.run):
            return INSTALLER.plan_fastbrick("fb", "SERIAL", target, amonet, cache)

    def test_converted_kaeru_unit_is_never_bricked_for_either_target(self):
        for target, spec in TARGETS.items():
            with self.subTest(target=target):
                fake = FakeFastboot(spec["product"], kaeru_mode="kaeru", lk=spec["lk_ok"][0])
                self.assertIsNone(self.plan(fake, target, amonet="x.zip"))
                self.assertFalse(any("flash" in c for c in fake.calls))

    def test_stock_locked_unit_gets_a_plan_for_either_target(self):
        for target, spec in TARGETS.items():
            with self.subTest(target=target):
                archive = Path(tempfile.mkdtemp()) / "stub.zip"
                fake = FakeFastboot(spec["product"], kaeru_mode="stock", lk=spec["lk_ok"][0])
                cache = Path(tempfile.mkdtemp())
                with mock.patch.object(INSTALLER, "_run_command", side_effect=fake.run), \
                        mock.patch.object(INSTALLER, "extract_amonet_payload", return_value=archive) as extract:
                    lk, staged = INSTALLER.plan_fastbrick("fb", "SERIAL", target, "x.zip", cache)
                self.assertEqual(lk, spec["lk_ok"][0])
                self.assertEqual(staged, archive)
                extract.assert_called_once()

    def test_indeterminate_kaeru_reply_fails_closed(self):
        for target, spec in TARGETS.items():
            for mode in ("silent", "mixed"):
                with self.subTest(target=target, mode=mode):
                    fake = FakeFastboot(spec["product"], kaeru_mode=mode, lk=spec["lk_ok"][0])
                    with self.assertRaisesRegex(INSTALLER.InstallerError, "could not be determined"):
                        self.plan(fake, target, amonet="x.zip")
                    self.assertFalse(any("flash" in c for c in fake.calls))

    def test_transport_errors_are_never_read_as_a_stock_unit(self):
        errors = (
            "FAILED (command write failed (No such device))",
            "FAILED (status read failed (Protocol error))",
            "fastboot: error: usb_write failed with status e00002ed\nFAILED (command write failed (Unknown error))",
            "FAILED (unable to open device)",
            "FAILED",
        )
        for target, spec in TARGETS.items():
            for text in errors:
                with self.subTest(target=target, text=text):
                    fake = FakeFastboot(spec["product"], kaeru_mode="stock", lk=spec["lk_ok"][0])
                    original = fake.run
                    def run(argv, timeout, *, check=True, _o=original, _t=text):
                        if argv[-2:] == ["oem", "kaeru-version"]:
                            fake.calls.append(list(argv))
                            return completed(argv, 1, "", _t)
                        return _o(argv, timeout, check=check)
                    with mock.patch.object(INSTALLER, "_run_command", side_effect=run):
                        with self.assertRaisesRegex(INSTALLER.InstallerError, "could not be determined"):
                            INSTALLER.plan_fastbrick("fb", "SERIAL", target, "x.zip", Path(tempfile.mkdtemp()))
                    self.assertFalse(any("flash" in c for c in fake.calls))

    def test_every_real_stock_refusal_wording_is_accepted_as_stock(self):
        wordings = ("FAILunknown command\n", "FAILED (remote: 'unknown command')",
                    "FAILED (remote: 'the command you input is restricted on locked hw')\nfastboot: error: Command failed",
                    "FAILED (remote: 'not allowed in locked state')", "FAILED (remote: 'unsupported command')")
        for text in wordings:
            with self.subTest(text=text):
                with mock.patch.object(INSTALLER, "_run_command", return_value=completed(["x"], 1, "", text)):
                    self.assertIsNone(INSTALLER.identify_kaeru("fb", "SERIAL"))

    def test_already_unlocked_unit_skips_the_brick_without_probing_kaeru(self):
        fake = FakeFastboot("RADAR", unlocked=True, kaeru_mode="silent", lk=RADAR_NEW)
        self.assertIsNone(self.plan(fake, amonet=None))
        self.assertFalse(any(c[-2:] == ["oem", "kaeru-version"] for c in fake.calls))

    def test_unrecognised_unlock_status_refuses_before_any_write(self):
        for raw in ("", "maybe", "unknown-state"):
            with self.subTest(raw=raw):
                fake = FakeFastboot("BISCUIT", kaeru_mode="stock", lk=BISCUIT_LK)
                fake.unlock_raw = raw
                with mock.patch.object(INSTALLER, "_run_command", side_effect=fake.run), \
                        self.assertRaisesRegex(INSTALLER.InstallerError, "not a recognised true/false"):
                    INSTALLER.plan_fastbrick("fb", "SERIAL", "biscuit", "x.zip", Path(tempfile.mkdtemp()))
                self.assertFalse(any("flash" in c for c in fake.calls))

    def test_unlocked_synonyms_skip_the_brick_as_the_web_installer_does(self):
        for raw in ("true", "unlocked", "yes", "1"):
            with self.subTest(raw=raw):
                fake = FakeFastboot("RADAR", kaeru_mode="silent", lk=RADAR_NEW)
                fake.unlock_raw = raw
                with mock.patch.object(INSTALLER, "_run_command", side_effect=fake.run):
                    self.assertIsNone(INSTALLER.plan_fastbrick("fb", "SERIAL", "radar_puffin", None, Path(tempfile.mkdtemp())))

    def test_missing_archive_is_refused_before_any_write(self):
        fake = FakeFastboot("BISCUIT", kaeru_mode="stock", lk=BISCUIT_LK)
        with self.assertRaisesRegex(INSTALLER.InstallerError, "requires --amonet-zip"):
            self.plan(fake, "biscuit", amonet=None)
        self.assertFalse(any("flash" in c for c in fake.calls))


class ZipHardeningTests(unittest.TestCase):
    def pin_for(self, target, payload_name, data):
        return {"payload": payload_name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def run_extract(self, archive, target, entry):
        # Redirect the archive-level pin to the synthetic archive; the member logic is what is under test.
        original = INSTALLER.AMONET_PINS[target]
        size, digest = archive.stat().st_size, INSTALLER._sha256(archive)
        patched = {**original, "archive_size": size, "archive_sha256": digest}
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        with mock.patch.dict(INSTALLER.AMONET_PINS, {target: patched}):
            return INSTALLER.extract_amonet_payload(archive, target, entry, tmp)

    def test_traversal_payload_names_in_pin_are_refused(self):
        tmp = Path(tempfile.mkdtemp())
        archive = make_archive(tmp / "a.zip", {"amonet/bin/x.img": b"data"})
        for bad in ("../x.img", "a/b.img", "..", "", "a\\b.img"):
            with self.subTest(name=bad), self.assertRaisesRegex(INSTALLER.InstallerError, "unsafe payload name"):
                self.run_extract(archive, "biscuit", {"payload": bad, "size": 4, "sha256": "0" * 64})

    def test_duplicate_member_is_refused(self):
        tmp = Path(tempfile.mkdtemp())
        archive = tmp / "dup.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("amonet/bin/fastbrick.img", b"one")
            bundle.writestr("amonet/bin/fastbrick.img", b"two")
        with self.assertRaisesRegex(INSTALLER.InstallerError, "exactly one"):
            self.run_extract(archive, "radar_puffin", {"payload": "fastbrick.img", "size": 3, "sha256": "0" * 64})

    def test_symlink_member_is_refused(self):
        tmp = Path(tempfile.mkdtemp())
        archive = make_archive(tmp / "sym.zip", {"amonet/bin/fastbrick.img": b"target"},
                               symlink="amonet/bin/fastbrick.img")
        with self.assertRaisesRegex(INSTALLER.InstallerError, "not a regular file"):
            self.run_extract(archive, "radar_puffin", {"payload": "fastbrick.img", "size": 6, "sha256": "0" * 64})

    def test_wrong_payload_bytes_never_leave_a_destination_file(self):
        tmp = Path(tempfile.mkdtemp())
        archive = make_archive(tmp / "ok.zip", {"amonet/bin/fastbrick.img": b"0123456789"})
        entry = {"payload": "fastbrick.img", "size": 10, "sha256": "f" * 64}
        with self.assertRaisesRegex(INSTALLER.InstallerError, "does not match its pin"):
            self.run_extract(archive, "radar_puffin", entry)


class BrickOutcomeTests(unittest.TestCase):
    def brick(self, outcomes, budget=0):
        payload = Path(tempfile.mkdtemp()) / "fastbrick.img"
        payload.write_bytes(b"x")
        seq = iter(outcomes)

        def fake_run(argv, timeout=None, **kwargs):
            item = next(seq)
            if isinstance(item, BaseException):
                raise item
            return item

        with mock.patch.object(INSTALLER.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(INSTALLER.time, "sleep"), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            INSTALLER.brick_fastboot_payload("fb", "SERIAL", payload, budget)
        return out.getvalue()

    def test_timeout_is_recorded_as_unknown_outcome_not_success(self):
        out = self.brick([subprocess.TimeoutExpired("fb", 8)])
        self.assertIn("outcome unknown", out)
        self.assertNotIn("expected", out)

    def test_emmc_ro_is_terminal_and_never_retried(self):
        outcomes = [completed([], 1, "", "FAILeMMC-RO"), completed([], 0, "", "")]
        with self.assertRaisesRegex(INSTALLER.InstallerError, "read-only"):
            self.brick(outcomes)
        self.assertEqual(len(outcomes), 2)  # second outcome never consumed

    def test_device_mismatch_is_terminal_for_either_target(self):
        with self.assertRaisesRegex(INSTALLER.InstallerError, "Device mismatch"):
            self.brick([completed([], 1, "", "FAILDevice mismatch")])

    def test_success_without_leaving_fastboot_is_refused(self):
        with self.assertRaisesRegex(INSTALLER.InstallerError, "did not leave fastboot"):
            self.brick([completed([], 0, "OKAY", "")])

    def test_nonzero_non_terminal_failure_is_not_retried_by_the_brick_step(self):
        # A nonzero transport-style reply is an unknown outcome: one send, no auto re-send.
        payload = Path(tempfile.mkdtemp()) / "fastbrick.img"
        payload.write_bytes(b"x")
        for text in ("FAILother", "FAILED (command write failed (Success))", "FAILED (status read failed (Protocol error))"):
            with self.subTest(text=text):
                calls = []

                def fake_run(argv, timeout=None, **kwargs):
                    calls.append(argv)
                    if len(calls) > 5:
                        raise AssertionError("brick step retried a nonzero unknown outcome")
                    return completed([], 1, "", text)

                with mock.patch.object(INSTALLER.subprocess, "run", side_effect=fake_run), \
                        mock.patch.object(INSTALLER.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()):
                    value = INSTALLER.brick_fastboot_payload("fb", "SERIAL", payload, 60)
                self.assertEqual(value, "unknown")
                self.assertEqual(len(calls), 1)
                sleep.assert_not_called()


class PostBrickIdentityTests(unittest.TestCase):
    def test_serial_change_across_brick_is_refused(self):
        with self.assertRaisesRegex(INSTALLER.InstallerError, "serial changed"):
            INSTALLER.confirm_post_brick_identity("fb", "AAA", "BBB", "biscuit")

    def test_explicit_override_confirms_cross_flashed_identity_after_brick(self):
        with mock.patch.object(INSTALLER, "_run_command",
                               return_value=completed([], 0, "product: BISCUIT\n", "")):
            INSTALLER.confirm_post_brick_identity("fb", "SER", "SER", "radar_puffin", "radar_puffin")

    def test_explicit_override_never_confirms_an_unreadable_product(self):
        with mock.patch.object(INSTALLER, "_run_command", return_value=completed([], 1, "", "FAILED (command write failed)")), \
                self.assertRaises(INSTALLER.FastbootTransportError):
            INSTALLER.confirm_post_brick_identity("fb", "SER", "SER", "radar_puffin", "radar_puffin")

    def test_product_mismatch_after_brick_is_refused_for_each_target(self):
        for target, wrong in (("radar_puffin", "BISCUIT"), ("biscuit", "RADAR")):
            with self.subTest(target=target), \
                    mock.patch.object(INSTALLER, "_run_command",
                                      return_value=completed([], 0, f"product: {wrong}\n", "")):
                with self.assertRaises(INSTALLER.InstallerError):
                    INSTALLER.confirm_post_brick_identity("fb", "SER", "SER", target)

    def test_matching_product_after_brick_is_accepted(self):
        for target, product in (("radar_puffin", "RADAR"), ("biscuit", "BISCUIT")):
            with self.subTest(target=target), \
                    mock.patch.object(INSTALLER, "_run_command",
                                      return_value=completed([], 0, f"product: {product}\n", "")):
                INSTALLER.confirm_post_brick_identity("fb", "SER", "SER", target)


class NoExpdbWriteTests(unittest.TestCase):
    def test_no_expdb_erase_or_write_exists_in_the_installer(self):
        source = (ROOT / "tools/libreecho-install.py").read_text()
        for needle in ("erase expdb", "flash expdb", '"expdb"', "'expdb'"):
            self.assertNotIn(needle, source, needle)

    def test_no_command_touching_expdb_is_ever_issued_on_the_locked_path(self):
        for target, spec in TARGETS.items():
            with self.subTest(target=target):
                fake = FakeFastboot(spec["product"], kaeru_mode="stock", lk=spec["lk_ok"][0])
                cache = Path(tempfile.mkdtemp())
                payload = Path(tempfile.mkdtemp()) / "fastbrick.img"
                payload.write_bytes(b"stub payload")
                with mock.patch.object(INSTALLER, "_run_command", side_effect=fake.run), \
                        mock.patch.object(INSTALLER, "extract_amonet_payload", return_value=payload), \
                        mock.patch.object(INSTALLER.subprocess, "run",
                                          side_effect=subprocess.TimeoutExpired("fb", 8)), \
                        mock.patch.object(INSTALLER, "wait_for_fastboot_serial", return_value="SERIAL"):
                    INSTALLER.plan_fastbrick("fb", "SERIAL", target, "x.zip", cache)
                    INSTALLER.brick_fastboot_payload("fb", "SERIAL", payload, 0)
                self.assertFalse(any("expdb" in " ".join(c) for c in fake.calls), fake.calls)


class PackagedHostFastbootTests(unittest.TestCase):
    """Bundled fastboot is hash-pinned and selected by architecture; never executed unless it matches."""

    def stage(self, archive, machine, tools, cache, target="radar_puffin"):
        pin = INSTALLER.AMONET_PINS[target]
        patched = {**pin, "archive_size": archive.stat().st_size, "archive_sha256": INSTALLER._sha256(archive)}
        with mock.patch.dict(INSTALLER.AMONET_PINS, {target: patched}), \
                mock.patch.dict(INSTALLER.AMONET_HOST_TOOLS, tools, clear=True):
            return INSTALLER.stage_amonet_host_fastboot(archive, target, cache, machine=machine)

    def test_reviewed_host_tool_pins_match_the_packaged_binaries(self):
        self.assertEqual(INSTALLER.AMONET_HOST_TOOLS["fastboot"], {
            "machine": "x86_64", "size": 7314048,
            "sha256": "cb3d13b850143da85eb4b2099462514894459213da93d03d6ff4782d927d6e20"})
        self.assertEqual(INSTALLER.AMONET_HOST_TOOLS["fastboot32"], {
            "machine": "i386", "size": 7050644,
            "sha256": "8912e9926cfc45503ad960866501305a684462b1dd1a44c5d82b1f075d6dd8c1"})

    def test_matching_architecture_extracts_only_the_pinned_bytes(self):
        data = b"pinned fastboot bytes"
        tmp = Path(tempfile.mkdtemp())
        archive = make_archive(tmp / "a.zip", {"amonet/bin/fastboot": data})
        tools = {"fastboot": {"machine": "x86_64", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        cache = tmp / "cache"
        path = self.stage(archive, "x86_64", tools, cache)
        self.assertEqual(path.read_bytes(), data)
        self.assertEqual(path.stat().st_mode & 0o777, 0o755)

    def test_incompatible_architecture_refuses_before_extraction(self):
        data = b"pinned fastboot bytes"
        tmp = Path(tempfile.mkdtemp())
        archive = make_archive(tmp / "a.zip", {"amonet/bin/fastboot": data})
        tools = {"fastboot": {"machine": "x86_64", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
        cache = tmp / "cache"
        with self.assertRaisesRegex(INSTALLER.InstallerError, "no packaged fastboot for architecture aarch64"):
            self.stage(archive, "aarch64", tools, cache)
        self.assertFalse(cache.exists() and any(cache.rglob("*")))

    def test_host_tool_hash_or_size_drift_is_refused(self):
        data = b"tampered fastboot bytes"
        tmp = Path(tempfile.mkdtemp())
        archive = make_archive(tmp / "a.zip", {"amonet/bin/fastboot": data})
        for tool in ({"machine": "x86_64", "size": len(data), "sha256": "0" * 64},
                     {"machine": "x86_64", "size": len(data) + 1, "sha256": hashlib.sha256(data).hexdigest()}):
            with self.subTest(tool=tool), self.assertRaisesRegex(INSTALLER.InstallerError, "does not match its pin"):
                self.stage(archive, "x86_64", {"fastboot": tool}, tmp / "cache")

    def test_archive_that_is_not_the_pinned_zip_is_refused(self):
        tmp = Path(tempfile.mkdtemp())
        archive = make_archive(tmp / "other.zip", {"amonet/bin/fastboot": b"x"})
        with self.assertRaisesRegex(INSTALLER.InstallerError, "does not match the pinned"):
            INSTALLER.stage_amonet_host_fastboot(archive, "radar_puffin", tmp / "cache", machine="x86_64")


class PayloadOutcomeTests(unittest.TestCase):
    def run_brick(self, outcomes, budget=0, log_path=None):
        payload = Path(tempfile.mkdtemp()) / "fastbrick.img"
        payload.write_bytes(b"x")
        seq = iter(outcomes)
        consumed = []

        def fake_run(argv, timeout=None, **kwargs):
            consumed.append(argv)
            item = next(seq)
            if isinstance(item, BaseException):
                raise item
            return item

        patches = [mock.patch.object(INSTALLER.subprocess, "run", side_effect=fake_run),
                   mock.patch.object(INSTALLER.time, "sleep")]
        if log_path is not None:
            patches.append(mock.patch.object(INSTALLER, "ACTIVE_LOG_PATH", log_path))
        with contextlib.ExitStack() as stack, contextlib.redirect_stdout(io.StringIO()):
            for patch in patches:
                stack.enter_context(patch)
            value = INSTALLER.brick_fastboot_payload("fb", "SERIAL", payload, budget)
        return value, len(consumed)

    def test_timeout_returns_unknown_outcome_for_reconciliation(self):
        value, _ = self.run_brick([subprocess.TimeoutExpired("fb", 8)])
        self.assertEqual(value, "unknown")

    def test_timeout_partial_output_is_appended_to_the_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "run.log"
            log.write_text("", encoding="utf-8")
            exc = subprocess.TimeoutExpired("fb", 8, output="partial-stdout-marker", stderr="partial-stderr-marker")
            self.run_brick([exc], log_path=log)
            text = log.read_text(encoding="utf-8")
            self.assertIn("partial-stdout-marker", text)
            self.assertIn("partial-stderr-marker", text)

    def test_each_failed_attempt_is_logged_with_return_code_and_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "run.log"
            log.write_text("", encoding="utf-8")
            self.run_brick([completed([], 1, "", "FAILED (command write failed (Success))"),
                            subprocess.TimeoutExpired("fb", 8)], budget=60, log_path=log)
            text = log.read_text(encoding="utf-8")
            self.assertIn("attempt 1 rc=1", text)
            self.assertIn("command write failed (Success)", text)

    def test_bootloader_refusal_stops_at_first_attempt_not_at_the_deadline(self):
        refused = completed([], 1, "", "FAILED (remote: 'unknown command')")
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "run.log"
            log.write_text("", encoding="utf-8")
            with mock.patch.object(INSTALLER, "ACTIVE_LOG_PATH", log), self.assertRaisesRegex(
                    INSTALLER.InstallerError, "refused the brick payload"):
                self.run_brick([refused] * 3, budget=60)
        # Exactly one brick command was issued; the refusal did not loop to the deadline.
        payload = Path(tempfile.mkdtemp()) / "fastbrick.img"
        payload.write_bytes(b"x")
        calls = []

        def fake_run(argv, timeout=None, **kwargs):
            calls.append(argv)
            return refused

        with mock.patch.object(INSTALLER.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(INSTALLER.time, "sleep") as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(INSTALLER.InstallerError):
                INSTALLER.brick_fastboot_payload("fb", "SERIAL", payload, 60)
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()


class ProductIdentityTests(unittest.TestCase):
    def product(self, text, rc=0, target="radar_puffin", override=None):
        with mock.patch.object(INSTALLER, "_run_command", return_value=completed([], rc, text, "")):
            return INSTALLER.verify_fastboot_product("fb", "SER", target, override)

    def test_empty_or_failed_product_is_a_transport_error_not_a_mismatch(self):
        for text, rc in (("", 1), ("", 0), ("FAILED (command write failed (Success))", 1)):
            with self.subTest(text=text, rc=rc), self.assertRaises(INSTALLER.FastbootTransportError):
                self.product(text, rc)

    def test_override_never_applies_to_an_unreadable_product(self):
        for text, rc in (("", 1), ("FAILED (command write failed (Success))", 1)):
            with self.subTest(text=text), self.assertRaises(INSTALLER.FastbootTransportError):
                self.product(text, rc, target=None, override="biscuit")

    def test_real_product_mismatch_is_terminal_and_not_transport(self):
        with self.assertRaises(INSTALLER.InstallerError) as raised:
            self.product("product: BISCUIT\n", 0, target="radar_puffin")
        self.assertNotIsInstance(raised.exception, INSTALLER.FastbootTransportError)

    def test_explicit_cross_flash_override_still_accepted_for_a_readable_product(self):
        self.assertEqual(self.product("product: BISCUIT\n", 0, target=None, override="biscuit"), "biscuit")


def expdb_image(*, lk_size=0x3AA40, kaeru_name=b"kaeru\x00", kaeru_ext=0x58891689, kaeru_delta=0):
    """Synthetic expdb with the upstream chain: LK header, then Kaeru at 512 + round8(LK data).

    Layout mirrors stage1/lkloader.c: magic at 0, data size at 4, name at 8, extended magic at 48.
    """
    lk = bytearray(512)
    struct.pack_into("<II", lk, 0, 0x58881688, lk_size)
    lk[8:11] = b"LK\x00"
    struct.pack_into("<I", lk, 48, 0xFFFFFFFF)
    offset = 512 + ((lk_size + 7) & ~7) + kaeru_delta
    kaeru = bytearray(512)
    struct.pack_into("<II", kaeru, 0, 0x58881688, 0x5B1C)
    kaeru[8:8 + len(kaeru_name)] = kaeru_name
    struct.pack_into("<I", kaeru, 48, kaeru_ext)
    image = bytearray(offset + 512)
    image[0:512] = lk
    image[offset:offset + 512] = kaeru
    return bytes(image)


class FakeHandoffDevice:
    """Scripted same-serial device: fastboot/ADB presence, product/unlock replies, TWRP marker,
    sysfs-visible partition names, and a block image served only through `od -j/-N`."""

    def __init__(self, *, fastboot=True, product=("RADAR",), unlock=("true",), adb=False,
                 device_prop="radar", marker="3.7.0", partnames=None, image=None):
        self.fastboot, self.adb = fastboot, adb
        self.product, self.unlock = list(product), list(unlock)
        self.device_prop, self.marker = device_prop, marker
        self.partnames = {"mmcblk0p9": "expdb", "mmcblk0p10": "boot_a_x"} if partnames is None else partnames
        self.image = expdb_image() if image is None else image
        self.calls, self.reboots, self.block_reads = [], 0, []

    def _next(self, values):
        return values.pop(0) if len(values) > 1 else values[0]

    def run(self, argv, timeout, *, check=True):
        self.calls.append(list(argv))
        if argv[0] == "fb":
            return self._fastboot(argv)
        if argv[0] == "adb":
            return self._adb(argv)
        return completed(argv, 0, "")

    def _fastboot(self, argv):
        if argv[1:] == ["devices"]:
            return completed(argv, 0, "SER\tfastboot\n" if self.fastboot else "")
        if argv[-1] == "product":
            value = self._next(self.product)
            if value is None:
                return completed(argv, 1, "", "FAILED (command write failed (Success))")
            return completed(argv, 0, f"product: {value}\n" if value else "")
        if argv[-1] == "unlock_status":
            return completed(argv, 0, f"unlock_status: {self._next(self.unlock)}\n")
        if argv[-2:] == ["oem", "kaeru-version"]:
            return completed(argv, 1, "", "FAILunknown command\n")
        return completed(argv, 0, "")

    def _adb(self, argv):
        text = " ".join(argv)
        if argv[1:] == ["devices"]:
            return completed(argv, 0, "List of devices attached\n" + ("SER\tdevice\n" if self.adb else ""))
        if argv[-2:] == ["reboot", "bootloader"]:
            self.reboots += 1
            self.adb, self.fastboot = False, True
            return completed(argv, 0, "")
        if text.endswith("getprop ro.product.device"):
            return completed(argv, 0, self.device_prop + "\n")
        if text.endswith("getprop ro.twrp.version"):
            return completed(argv, 0, (self.marker or "") + "\n")
        if "/sys/class/block/*/uevent" in argv[-1]:
            hits = [f"/sys/class/block/{n}/uevent" for n, p in self.partnames.items() if p == "expdb"]
            return completed(argv, 0 if hits else 1, "".join(h + "\n" for h in hits))
        if argv[4:5] == ["od"]:
            offset = int(argv[argv.index("-j") + 1])
            count = int(argv[argv.index("-N") + 1])
            path = argv[-1]
            self.block_reads.append(path)
            name = path.removeprefix("/dev/block/")
            if path.startswith("/dev/block/") and name in self.partnames:
                data = self.image[offset:offset + count]
                return completed(argv, 0 if len(data) == count else 1,
                                 " ".join(f"{b:02x}" for b in data) + "\n")
            return completed(argv, 1, "", "no such block device")
        return completed(argv, 0, "")


class ExpdbChainTests(unittest.TestCase):
    """LK -> Kaeru chained header identity, checked on synthetic and upstream-shipped images."""

    def check(self, image):
        return INSTALLER._expdb_kaeru_chain_ok(lambda off, n: image[off:off + n])

    def test_synthetic_upstream_layout_is_accepted(self):
        self.assertTrue(self.check(expdb_image()))

    def test_recovery_reader_accepts_real_od_output_with_repeated_zero_rows(self):
        # Real od collapses duplicate rows unless -v is supplied. Do not let a
        # byte-list mock hide rejection of otherwise valid recovery headers.
        device = FakeHandoffDevice()
        with tempfile.TemporaryDirectory() as scratch:
            image = Path(scratch) / "expdb"
            image.write_bytes(expdb_image())

            def run(argv, timeout, *, check=True):
                if argv[4:5] == ["od"]:
                    return subprocess.run([*argv[4:-1], str(image)],
                                          text=True, capture_output=True, timeout=timeout)
                return device.run(argv, timeout, check=check)

            with mock.patch.object(INSTALLER, "_run_command", side_effect=run):
                self.assertTrue(INSTALLER._adb_recovery_is_verified("adb", "SER", "radar_puffin"))

    def test_wrong_name_extended_magic_lk_magic_or_offset_is_refused(self):
        for label, image in (
            ("name", expdb_image(kaeru_name=b"kaer\x00\x00")),
            ("ext-magic", expdb_image(kaeru_ext=0)),
            ("offset", expdb_image(kaeru_delta=8)),
            ("lk-magic", expdb_image()[:0] + b"\x00\x00\x00\x00" + expdb_image()[4:]),
        ):
            with self.subTest(label=label):
                self.assertFalse(self.check(image))

    def test_truncated_read_is_refused(self):
        self.assertFalse(self.check(expdb_image()[:0x3AC40]))

    def test_upstream_shipped_radar_and_biscuit_blobs_satisfy_the_chain(self):
        for archive, member in ((ARCHIVE_ZIPS["radar_puffin"], "amonet/bin/radar-kaeru.bin"),
                                (ARCHIVE_ZIPS["biscuit"], "amonet/bin/biscuit-kaeru.bin")):
            with self.subTest(member=member):
                if not archive.exists():
                    self.skipTest(f"{archive} not present in this checkout")
                with zipfile.ZipFile(archive) as bundle:
                    blob = bundle.read(member)
                self.assertTrue(self.check(blob))
                # Upstream reference: LK data size is the pin; Kaeru header sits at 0x3ac40 (radar) / 0x3b470 (biscuit).
                expected = 0x3AC40 if "radar" in member else 0x3B470
                self.assertEqual(struct.unpack_from("<I", blob, 4)[0] + 512, expected)


class PostBrickHandoffTests(unittest.TestCase):
    def wait(self, device, timeout=0.3, target="radar_puffin", override=None):
        with mock.patch.object(INSTALLER, "_run_command", side_effect=device.run), \
                mock.patch.object(INSTALLER.subprocess, "run", side_effect=device.run), \
                mock.patch.object(INSTALLER.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            return INSTALLER.wait_for_post_brick_fastboot("fb", "adb", "SER", target, timeout, override=override)

    def test_transient_usb_product_error_is_retried_then_unlocked_fastboot_accepted(self):
        device = FakeHandoffDevice(product=(None, "", "RADAR"), unlock=("true",))
        self.assertEqual(self.wait(device), "SER")
        product_reads = [c for c in device.calls if c[-1] == "product"]
        self.assertGreaterEqual(len(product_reads), 3)

    def test_real_product_mismatch_after_brick_is_terminal_at_first_read(self):
        device = FakeHandoffDevice(product=("BISCUIT",))
        with self.assertRaises(INSTALLER.InstallerError) as raised:
            self.wait(device)
        self.assertNotIsInstance(raised.exception, INSTALLER.FastbootTransportError)
        self.assertEqual(len([c for c in device.calls if c[-1] == "product"]), 1)

    def test_locked_fastboot_is_never_accepted_as_handoff(self):
        device = FakeHandoffDevice(product=("RADAR",), unlock=("false",))
        with self.assertRaisesRegex(INSTALLER.InstallerError, "timed out"):
            self.wait(device, timeout=0.05)

    def test_verified_recovery_requests_bootloader_once_then_accepts_unlocked_fastboot(self):
        device = FakeHandoffDevice(fastboot=False, adb=True, device_prop="radar")
        self.assertEqual(self.wait(device), "SER")
        self.assertEqual(device.reboots, 1)

    def test_recovery_with_damaged_kaeru_header_never_requests_bootloader(self):
        device = FakeHandoffDevice(fastboot=False, adb=True, device_prop="radar",
                                   image=expdb_image(kaeru_ext=0))
        with self.assertRaisesRegex(INSTALLER.InstallerError, "timed out"):
            self.wait(device, timeout=0.05)
        self.assertEqual(device.reboots, 0)

    def test_recovery_of_another_board_never_requests_bootloader(self):
        device = FakeHandoffDevice(fastboot=False, adb=True, device_prop="biscuit")
        with self.assertRaises(INSTALLER.InstallerError):
            self.wait(device, timeout=0.05)
        self.assertEqual(device.reboots, 0)

    def test_stale_fastboot_listing_does_not_starve_verified_recovery_adb(self):
        # fastboot still lists the serial, but its product reads fail on transport. Recovery ADB
        # must still be checked in the same pass rather than spinning until the deadline.
        device = FakeHandoffDevice(fastboot=True, product=(None,), adb=True, device_prop="radar")
        with self.assertRaisesRegex(INSTALLER.InstallerError, "timed out"):
            self.wait(device, timeout=0.05)
        self.assertEqual(device.reboots, 1)

    def test_recovery_without_twrp_marker_never_requests_bootloader(self):
        device = FakeHandoffDevice(fastboot=False, adb=True, device_prop="radar", marker="")
        with self.assertRaises(INSTALLER.InstallerError):
            self.wait(device, timeout=0.05)
        self.assertEqual(device.reboots, 0)

    def test_expdb_is_resolved_through_sysfs_and_read_by_device_node_only(self):
        device = FakeHandoffDevice(fastboot=False, adb=True, device_prop="radar")
        self.assertEqual(self.wait(device), "SER")
        self.assertEqual(set(device.block_reads), {"/dev/block/mmcblk0p9"})

    def test_expdb_on_another_partition_index_is_verified_not_hardcoded_p7(self):
        device = FakeHandoffDevice(fastboot=False, adb=True, device_prop="radar",
                                   partnames={"mmcblk0p12": "expdb", "mmcblk0p7": "system"})
        self.assertEqual(self.wait(device), "SER")
        self.assertEqual(device.reboots, 1)
        self.assertNotIn("/dev/block/mmcblk0p7", device.block_reads)

    def test_ambiguous_expdb_partname_is_refused_without_reboot(self):
        device = FakeHandoffDevice(fastboot=False, adb=True, device_prop="radar",
                                   partnames={"mmcblk0p9": "expdb", "mmcblk0p12": "expdb"})
        with self.assertRaises(INSTALLER.InstallerError):
            self.wait(device, timeout=0.05)
        self.assertEqual(device.reboots, 0)
        self.assertEqual(device.block_reads, [])

    def test_explicit_cross_flash_override_is_threaded_through_the_handoff(self):
        device = FakeHandoffDevice(product=("BISCUIT",), unlock=("true",))
        self.assertEqual(self.wait(device, target="radar_puffin", override="radar_puffin"), "SER")

    def test_missing_product_is_never_accepted_even_with_an_override(self):
        device = FakeHandoffDevice(product=(None,), unlock=("true",))
        with self.assertRaises(INSTALLER.InstallerError):
            self.wait(device, timeout=0.05, target="radar_puffin", override="radar_puffin")


if __name__ == "__main__":
    unittest.main()
