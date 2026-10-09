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
BISCUIT_LK = "63cb91b-20221007_072309"
TARGETS = {
    "radar_puffin": {"product": "RADAR", "lk_ok": (RADAR_OLD, RADAR_NEW)},
    "biscuit": {"product": "BISCUIT", "lk_ok": (BISCUIT_LK,)},
}
ARCHIVE_NAMES = {"radar_puffin": "amonet-radar-v1.0.0.zip", "biscuit": "amonet-biscuit-v2.0.0.zip"}
REAL_ARCHIVES = [Path.home() / "Downloads" / name for name in ARCHIVE_NAMES.values()]


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

    def test_timeout_is_the_expected_success_signal(self):
        out = self.brick([subprocess.TimeoutExpired("fb", 8)])
        self.assertIn("expected", out)

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

    def test_repeated_non_terminal_failure_stops_at_deadline_not_forever(self):
        with self.assertRaisesRegex(INSTALLER.InstallerError, "did not complete within the timeout"):
            self.brick([completed([], 1, "", "FAILother")] * 5, budget=-1)


class PostBrickIdentityTests(unittest.TestCase):
    def test_serial_change_across_brick_is_refused(self):
        with self.assertRaisesRegex(INSTALLER.InstallerError, "serial changed"):
            INSTALLER.confirm_post_brick_identity("fb", "AAA", "BBB", "biscuit")

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


if __name__ == "__main__":
    unittest.main()
