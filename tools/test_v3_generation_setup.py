"""v3 signed-generation publication and CLI first-boot setup (web-installer parity)."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
RELEASE = "radar-puffin-build-83bee8c-0123456789abcdef-0123456789abcdef"
FEATURES = ("airplay2", "tts", "wakeword", "stt", "assistant")


def load_installer():
    spec = importlib.util.spec_from_file_location("libreecho_install_v3", ROOT / "tools" / "libreecho-install.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_installer()


def payloads() -> dict[str, dict[str, bytes]]:
    return {name: {"payload": f"squash-{name}".encode() * 7, "manifest": f'{{"id":"{name}"}}'.encode()}
            for name in FEATURES}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def target_manifest(files, *, release=RELEASE, board="radar_puffin", boot="ab" * 32, tx="txn-0123abcd",
                    fmt="libreecho-ota-v3", features=",".join(FEATURES)) -> bytes:
    lines = [f"format={fmt}", f"board={board}", f"release={release}", f"transaction_id={tx}",
             f"boot_sha256={boot}", f"feature_ids={features}"]
    for name in FEATURES:
        lines += [f"feature_{name}_sha256={sha(files[name]['payload'])}",
                  f"feature_{name}_size={len(files[name]['payload'])}",
                  f"feature_{name}_manifest_sha256={sha(files[name]['manifest'])}",
                  f"feature_{name}_manifest_size={len(files[name]['manifest'])}"]
    return ("\n".join(lines) + "\n").encode()


def write_ota(directory: Path, manifest: bytes, signature: bytes = b"SIG", release: str = RELEASE) -> Path:
    path = directory / f"{installer.target_asset_prefix(release, 'radar_puffin')}.ota.tar"
    with tarfile.open(path, "w") as archive:
        for name, data in (("manifest", manifest), ("manifest.sig", signature), ("boot.img", b"boot")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def bundle_manifest(files, boot="ab" * 32) -> dict:
    return {"release": RELEASE, "boot": {"sha256": boot},
            "features": [{"name": name,
                          "payload": {"sha256": sha(files[name]["payload"]), "size": len(files[name]["payload"])},
                          "manifest": {"sha256": sha(files[name]["manifest"]), "size": len(files[name]["manifest"])}}
                         for name in FEATURES]}


class V3TargetTests(unittest.TestCase):
    def test_loads_signed_target_and_matches_staged_bundle(self) -> None:
        files = payloads()
        with tempfile.TemporaryDirectory() as temporary:
            raw = target_manifest(files)
            write_ota(Path(temporary), raw, b"SIGNATURE")
            v3 = installer.load_v3_target(temporary, RELEASE, "radar_puffin")
        self.assertEqual(v3["transaction_id"], "txn-0123abcd")
        self.assertEqual(v3["manifest"], raw)
        self.assertEqual(v3["signature"], b"SIGNATURE")
        self.assertEqual(v3["manifest_sha256"], sha(raw))
        installer._v3_matches_bundle(v3, bundle_manifest(files))

    def test_pre_v3_release_is_not_a_generation(self) -> None:
        files = payloads()
        with tempfile.TemporaryDirectory() as temporary:
            write_ota(Path(temporary), target_manifest(files, fmt="libreecho-ota-v2"))
            self.assertIsNone(installer.load_v3_target(temporary, RELEASE, "radar_puffin"))
            self.assertIsNone(installer.load_v3_target(Path(temporary) / "absent", RELEASE, "radar_puffin"))

    def test_rejects_target_bound_elsewhere_or_partial(self) -> None:
        files = payloads()
        for kwargs in ({"board": "biscuit"}, {"tx": "../escape"}, {"features": "airplay2,tts"}):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as temporary:
                write_ota(Path(temporary), target_manifest(files, **kwargs))
                with self.assertRaises(installer.InstallerError):
                    installer.load_v3_target(temporary, RELEASE, "radar_puffin")

    def test_staged_payload_that_differs_from_signed_target_is_refused(self) -> None:
        files = payloads()
        with tempfile.TemporaryDirectory() as temporary:
            write_ota(Path(temporary), target_manifest(files))
            v3 = installer.load_v3_target(temporary, RELEASE, "radar_puffin")
        tampered = bundle_manifest(files)
        tampered["features"][2]["payload"]["sha256"] = "00" * 32
        with self.assertRaisesRegex(installer.InstallerError, "wakeword"):
            installer._v3_matches_bundle(v3, tampered)
        with self.assertRaisesRegex(installer.InstallerError, "boot"):
            installer._v3_matches_bundle(v3, bundle_manifest(files, boot="cd" * 32))


class GenerationPublisherScriptTests(unittest.TestCase):
    """Run the real device-side publisher against a fake /data with stub device tools."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.files = payloads()
        self.raw = target_manifest(self.files)
        self.tx = "txn-0123abcd"
        libre = self.root / "data/libreecho"
        for name in FEATURES:
            feature = libre / "features" / name
            feature.mkdir(parents=True)
            (feature / "payload.squashfs").write_bytes(self.files[name]["payload"])
            (feature / "manifest.json").write_bytes(self.files[name]["manifest"])
        (libre / "update").mkdir()
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        (self.tmp / "libreecho-target.manifest").write_bytes(self.raw)
        (self.tmp / "libreecho-target.manifest.sig").write_bytes(b"SIG")
        (self.tmp / "libreecho-publish-generation.conf").write_text(
            f"TX={self.tx}\nMANIFEST_SHA256={sha(self.raw)}\n")
        self.sbin = self.root / "sbin"
        self.sbin.mkdir()
        self.verify_log = self.root / "verify.log"
        # Stub verifier: the generation's payloads must hash to the signed values.
        expected = "\n".join(f"{name} {sha(self.files[name]['payload'])}" for name in FEATURES)
        (self.sbin / "libreecho-generation").write_text(
            "#!/bin/sh\n"
            f"echo \"$@\" >> {self.verify_log}\n"
            "[ -f \"$2/COMPLETE\" ] || exit 1\n"
            f"printf '%s\\n' '{expected}' | while read name digest; do\n"
            "  [ \"$(sha256sum \"$2/features/$name/payload.squashfs\" | cut -d' ' -f1)\" = \"$digest\" ] || exit 1\n"
            "done\n")
        (self.sbin / "libreecho-target-manifest").write_text("#!/bin/sh\n[ \"$1\" = check ] && [ -s \"$3\" ]\n")
        for tool in self.sbin.iterdir():
            tool.chmod(0o755)

    def tearDown(self) -> None:
        subprocess.run(["chmod", "-R", "u+w", str(self.root)], check=False)
        self.temporary.cleanup()

    def script(self) -> str:
        source = installer.ROOT_GENERATION_PUBLISHER
        for old, new in (("/bin/busybox", ""), ("/tmp/", f"{self.tmp}/"), ("/data/libreecho", f"{self.root}/data/libreecho"),
                         ("/usr/local/sbin/", f"{self.sbin}/"), ("grep -q ' /data ' /proc/mounts", "true")):
            source = source.replace(old, new)
        return source.replace("BB=\n", "BB=env\n")

    def run_script(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", "-c", self.script()], text=True, capture_output=True, timeout=30)

    def test_publishes_complete_generation_then_current_then_retires_v2_tree(self) -> None:
        result = self.run_script()
        self.assertIn(f"GENERATION_OK:{self.tx}", result.stdout, result.stdout + result.stderr)
        libre = self.root / "data/libreecho"
        generation = libre / "generations" / self.tx
        self.assertEqual((libre / "update/current").read_text(), f"{self.tx}\n")
        self.assertEqual((generation / "COMPLETE").read_text(), f"{sha(self.raw)}\n")
        self.assertEqual((generation / "target.manifest").read_bytes(), self.raw)
        self.assertEqual(sorted(p.name for p in (generation / "features").iterdir()), sorted(FEATURES))
        self.assertFalse((libre / "features").exists())
        self.assertFalse((libre / "generations" / f"{self.tx}.partial").exists())
        self.assertFalse((self.tmp / "libreecho-publish-generation.conf").exists())
        # Idempotent: a second run only re-verifies the published generation.
        (self.tmp / "libreecho-target.manifest").write_bytes(self.raw)
        (self.tmp / "libreecho-target.manifest.sig").write_bytes(b"SIG")
        (self.tmp / "libreecho-publish-generation.conf").write_text(f"TX={self.tx}\nMANIFEST_SHA256={sha(self.raw)}\n")
        self.assertIn(f"GENERATION_OK:{self.tx}", self.run_script().stdout)

    def test_bad_payload_never_publishes_current(self) -> None:
        (self.root / "data/libreecho/features/stt/payload.squashfs").write_bytes(b"corrupt")
        result = self.run_script()
        self.assertIn("GENERATION_VERIFY_FAILED", result.stdout, result.stderr)
        self.assertFalse((self.root / "data/libreecho/update/current").exists())
        self.assertFalse((self.root / "data/libreecho/generations" / self.tx).exists())
        self.assertTrue((self.root / "data/libreecho/features/stt").exists())

    def test_manifest_hash_mismatch_and_pending_transaction_are_refused(self) -> None:
        (self.tmp / "libreecho-target.manifest").write_bytes(self.raw + b"x=1\n")
        self.assertIn("GENERATION_MANIFEST_HASH_MISMATCH", self.run_script().stdout)
        (self.tmp / "libreecho-target.manifest").write_bytes(self.raw)
        (self.tmp / "libreecho-target.manifest.sig").write_bytes(b"SIG")
        (self.tmp / "libreecho-publish-generation.conf").write_text(f"TX={self.tx}\nMANIFEST_SHA256={sha(self.raw)}\n")
        (self.root / "data/libreecho/update/pending").write_text("schema=3\n")
        self.assertIn("GENERATION_PENDING_PRESENT", self.run_script().stdout)
        self.assertFalse((self.root / "data/libreecho/update/current").exists())

    def test_conflicting_current_generation_is_never_replaced(self) -> None:
        (self.root / "data/libreecho/update/current").write_text("txn-other\n")
        self.assertIn("GENERATION_CURRENT_CONFLICT", self.run_script().stdout)
        self.assertEqual((self.root / "data/libreecho/update/current").read_text(), "txn-other\n")


def answers(**overrides):
    form = {"username": "Admin", "password": "correct horse", "password_confirm": "correct horse",
            "ssid": "Home", "security": "wpa2", "wifi_password": "network-pass", "hostname": "kitchen",
            "volume": 40, "wake_word": "Alexa", "wake_sensitivity": 55, "local_only": True, "telemetry": False}
    form.update(overrides)
    return form


class ProvisionParityTests(unittest.TestCase):
    def test_document_matches_web_installer_contract(self) -> None:
        raw = installer.build_provision_document(answers(), RELEASE, "radar_puffin", salt="1" * 64)
        document = json.loads(raw)
        self.assertEqual(list(document), ["schema", "binding", "admin", "wifi", "settings"])
        self.assertEqual(document["schema"], "libreecho-provision/1")
        self.assertEqual(document["binding"], {"release": RELEASE, "target": "radar-puffin"})
        digest = sha(("1" * 64 + ":correct horse").encode())
        self.assertEqual(document["admin"], {"users_line": f"admin:sha256:{'1' * 64}:{digest}"})
        self.assertEqual(document["wifi"], {"ssid": "Home", "security": "wpa2", "password": "network-pass"})
        self.assertEqual(document["settings"], {"hostname": "kitchen", "volume": 40, "wake_word": "Alexa",
                                                "wake_sensitivity": 55, "privacy_local_only": True,
                                                "privacy_telemetry": False})
        self.assertNotIn(b"correct horse", raw)

    def test_blank_ssid_omits_wifi_and_open_network_has_empty_password(self) -> None:
        document = json.loads(installer.build_provision_document(answers(ssid=""), RELEASE, "biscuit"))
        self.assertNotIn("wifi", document)
        self.assertEqual(document["binding"]["target"], "biscuit")
        document = json.loads(installer.build_provision_document(
            answers(security="open", wifi_password=""), RELEASE, "radar_puffin"))
        self.assertEqual(document["wifi"]["password"], "")

    def test_validation_rules_match_the_web_installer(self) -> None:
        cases = {
            "username": [{"username": ""}, {"username": "a" * 32}, {"username": "bad name"}],
            "password": [{"password": "short", "password_confirm": "short"},
                         {"password": "p" * 129, "password_confirm": "p" * 129}],
            "password_confirm": [{"password_confirm": "different!"}],
            "ssid": [{"ssid": "x" * 33}, {"ssid": "bad\nname"}],
            "security": [{"security": "wep"}],
            "wifi_password": [{"wifi_password": "short"}, {"security": "open", "wifi_password": "set"}],
            "hostname": [{"hostname": "-lead"}, {"hostname": "has space"}, {"hostname": "h" * 64},
                         {"hostname": "Correct-Horse", "password": "correct-horse", "password_confirm": "correct-horse"}],
            "volume": [{"volume": 101}, {"volume": "40"}, {"volume": True}],
            "wake_sensitivity": [{"wake_sensitivity": -1}],
            "wake_word": [{"wake_word": "Computer"}],
            "local_only": [{"local_only": None}],
        }
        self.assertEqual(installer.validate_provision(answers()), [])
        for field, variants in cases.items():
            for overrides in variants:
                with self.subTest(field=field, overrides=overrides):
                    fields = [f for f, _ in installer.validate_provision(answers(**overrides))]
                    self.assertIn(field, fields)

    def test_interactive_prompts_reask_until_valid_and_never_echo_secrets(self) -> None:
        replies = iter(["admin", "", "", "", "", "", "", "",                  # first pass: bad passwords
                        "admin", "Home", "y", "", "50", "", "", "n", "n", "y"])
        secrets = iter(["short", "short", "a-good-password", "a-good-password", "wifi-passphrase"])
        output: list[str] = []
        form = installer.collect_setup_answers(ask=lambda _p: next(replies), ask_secret=lambda _p: next(secrets),
                                               out=output.append)
        self.assertEqual(form["ssid"], "Home")
        self.assertEqual(form["wifi_password"], "wifi-passphrase")
        self.assertEqual(form["volume"], 50)
        self.assertFalse(form["local_only"])
        self.assertTrue(any("8-128" in line for line in output))
        self.assertFalse(any("a-good-password" in line or "wifi-passphrase" in line for line in output))

    def test_delivery_refuses_an_image_without_provision_support(self) -> None:
        probe = mock.Mock(returncode=0, stdout="DATA_OK\n0\n", stderr="")
        with mock.patch.object(installer, "_run_command", return_value=probe) as run:
            with self.assertRaisesRegex(installer.InstallerError, "web setup"):
                installer.deliver_provision("adb", "SERIAL", b"{}", tempfile.gettempdir())
        self.assertEqual(run.call_count, 1)

    def test_delivery_skips_a_device_that_already_finished_setup(self) -> None:
        probe = mock.Mock(returncode=0, stdout="DATA_OK\n1\nSETUP_DONE\n", stderr="")
        with mock.patch.object(installer, "_run_command", return_value=probe) as run:
            self.assertEqual(installer.deliver_provision("adb", "SERIAL", b"{}", tempfile.gettempdir()),
                             "already-complete")
        self.assertEqual(run.call_count, 1)

    def test_delivery_writes_atomically_and_removes_the_host_copy(self) -> None:
        document = installer.build_provision_document(answers(), RELEASE, "radar_puffin")
        calls = []

        def fake(argv, timeout, check=True):
            calls.append(argv)
            if argv[3:5] == ["push"] or argv[3] == "push":
                self.assertEqual(os.stat(argv[4]).st_mode & 0o777, 0o600)
            return mock.Mock(returncode=0, stdout="DATA_OK\n2\n", stderr="")

        with tempfile.TemporaryDirectory() as cache, \
                mock.patch.object(installer, "_run_command", side_effect=fake), \
                mock.patch.object(installer, "_adb_shell_text", return_value=str(len(document))):
            self.assertEqual(installer.deliver_provision("adb", "SERIAL", document, cache), "delivered")
            self.assertFalse((Path(cache) / "provision" / "provision.json").exists())
        pushes = [argv for argv in calls if argv[3] == "push"]
        self.assertEqual(pushes[0][5], "/data/libreecho/config/provision.json.tmp")
        self.assertTrue(any("mv -f /data/libreecho/config/provision.json.tmp /data/libreecho/config/provision.json"
                            in argv[-1] for argv in calls))


class FinishInstallTests(unittest.TestCase):
    def test_pre_v3_image_keeps_the_historical_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(installer, "_run_command") as run, \
                mock.patch.object(installer, "wait_for_startup_ready") as ready:
            self.assertEqual(installer.finish_install("adb", "S", temporary, temporary, bundle_manifest(payloads()),
                                                      "radar_puffin", None, 180), "web")
        run.assert_not_called()
        ready.assert_not_called()
    def test_v3_publishes_generation_reboots_and_waits_for_startup_ready(self) -> None:
        files = payloads()
        with tempfile.TemporaryDirectory() as temporary:
            write_ota(Path(temporary), target_manifest(files))
            with mock.patch.object(installer, "verify_v3_generation", return_value=False), \
                    mock.patch.object(installer, "publish_v3_generation") as publish, \
                    mock.patch.object(installer, "_run_command") as run, \
                    mock.patch.object(installer, "wait_for_startup_ready") as ready, \
                    mock.patch.object(installer.time, "sleep"):
                result = installer.finish_install("adb", "S", temporary, temporary, bundle_manifest(files),
                                                  "radar_puffin", None, 180)
        self.assertEqual(result, "web")
        publish.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["adb", "-s", "S", "reboot"])
        ready.assert_called_once()

    def test_already_current_generation_is_not_republished_or_rebooted(self) -> None:
        files = payloads()
        with tempfile.TemporaryDirectory() as temporary:
            write_ota(Path(temporary), target_manifest(files))
            with mock.patch.object(installer, "verify_v3_generation", return_value=True), \
                    mock.patch.object(installer, "publish_v3_generation") as publish, \
                    mock.patch.object(installer, "_run_command") as run, \
                    mock.patch.object(installer, "wait_for_startup_ready") as ready:
                installer.finish_install("adb", "S", temporary, temporary, bundle_manifest(files),
                                         "radar_puffin", None, 180)
        publish.assert_not_called()
        run.assert_not_called()
        ready.assert_called_once()

    def test_startup_ready_timeout_is_a_failure(self) -> None:
        with mock.patch.object(installer, "wait_for_transport"), \
                mock.patch.object(installer, "_adb_shell_text", return_value=""), \
                mock.patch.object(installer.time, "sleep"), \
                mock.patch.object(installer.time, "monotonic", side_effect=[0, 0, 10_000]):
            with self.assertRaisesRegex(installer.InstallerError, "startup-ready"):
                installer.wait_for_startup_ready("adb", "S", 300)


if __name__ == "__main__":
    unittest.main()
