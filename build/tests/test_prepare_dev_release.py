#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "build/ci/prepare-dev-release.py"
EMPTY = hashlib.sha256(b"").hexdigest()
FEATURES = ("airplay2", "tts", "wakeword", "stt", "assistant")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(root: Path) -> tuple[Path, dict[str, str]]:
    run = root / "run"
    features = run / "features"
    features.mkdir(parents=True)
    commits = {
        "product": "1" * 40,
        "platform": "2" * 40,
        "linux": "3" * 40,
        "ui": "4" * 40,
    }
    candidate = {
        "status": "PREPARED_NOT_FLASHED",
        "public_release_mode": "1",
        "update_channel": "dev",
        "image_profile": "ota",
        "service_profile": "production",
        "feature_policy": "community-noncommercial",
        "ssh_enabled": "0",
        "ota_signing_mode": "github",
        "ota_bundle": "",
        "ota_bundle_sha256": "",
        "product_git_head": commits["product"],
        "tooling_git_head": commits["platform"],
        "ui_commit": commits["ui"],
        "product_git_diff_sha256": EMPTY,
        "tooling_git_diff_sha256": EMPTY,
        "kernel_git_diff_sha256": EMPTY,
        "ui_diff_sha256": EMPTY,
    }
    boot = run / "boot.img"
    boot.write_bytes(b"boot")
    candidate["boot_image_sha256"] = digest(boot)
    for feature in FEATURES:
        key = "airplay" if feature == "airplay2" else feature
        payload = features / f"{feature}.squashfs"
        manifest = features / f"{feature}.manifest.json"
        payload.write_bytes((feature + " payload").encode())
        manifest.write_text(json.dumps({"feature": feature}) + "\n")
        candidate[f"{key}_payload_sha256"] = digest(payload)
        candidate[f"{key}_payload_size"] = str(payload.stat().st_size)
        candidate[f"{key}_feature_manifest_sha256"] = digest(manifest)
    (run / "CURRENT.candidate").write_text("".join(f"{k}={v}\n" for k, v in candidate.items()))
    (run / "provenance.txt").write_text(f"kernel_git_head={commits['linux'][:12]}\n")
    (run / "manifest.json").write_text(json.dumps({
        "connectivity": {
            "embedded_vendor_file_count": 0,
            "vendor_delivery": "owner-device-local-extraction",
        }
    }))
    (run / "release-source-commits.txt").write_text(
        "".join(f"{key}={value}\n" for key, value in commits.items())
    )
    (run / "verify.log").write_text(
        "arm32_recovery_image_contract=PASS status=PREPARED_NOT_FLASHED\n"
    )
    return run, commits


class Tests(unittest.TestCase):
    def test_prepares_bounded_unsigned_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, commits = fixture(root)
            output = root / "release"
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--artifact-root", str(root),
                "--output-dir", str(output),
                "--product-commit", commits["product"],
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            files = sorted(path.name for path in output.iterdir())
            self.assertEqual(len(files), 14)
            self.assertFalse(any("ota.tar" in name for name in files))
            manifest = json.loads(next(output.glob("*-build.json")).read_text())
            self.assertFalse(manifest["signed"])
            self.assertFalse(manifest["ota_bundle"])
            self.assertFalse(manifest["ssh_enabled"])
            self.assertEqual(manifest["ssh"]["dropbear_sha256"], "")
            self.assertEqual(manifest["ssh"]["dropbearkey_sha256"], "")
            self.assertEqual(manifest["status"], "PREPARED_NOT_FLASHED")
            self.assertRegex(manifest["source_set_id"], r"^[0-9a-f]{16}$")
            self.assertRegex(manifest["artifact_set_id"], r"^[0-9a-f]{16}$")
            self.assertIn(manifest["source_set_id"], result.stdout)
            self.assertIn(manifest["artifact_set_id"], result.stdout)
            sums = next(output.glob("*-SHA256SUMS"))
            check = subprocess.run(
                ["sha256sum", "-c", sums.name], cwd=output,
                text=True, capture_output=True,
            )
            self.assertEqual(check.returncode, 0, check.stderr)

    def test_prepares_bounded_unsigned_nightly_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, commits = fixture(root)
            output = root / "release"
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--artifact-root", str(root),
                "--output-dir", str(output),
                "--product-commit", commits["product"],
                "--release-kind", "nightly",
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("release_tag=radar-puffin-nightly-", result.stdout)

    def test_prepares_signed_dev_release_with_ota_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, commits = fixture(root)
            ota = run / "signed.ota.tar"
            ota.write_bytes(b"signed dev ota")
            candidate = run / "CURRENT.candidate"
            text = candidate.read_text()
            text = text.replace(
                "ota_signing_mode=github\n",
                "ota_signing_mode=local\n",
            )
            text = text.replace("ota_bundle=\n", "ota_bundle=" + str(ota) + "\n")
            text = text.replace("ota_bundle_sha256=\n", "ota_bundle_sha256=" + digest(ota) + "\n")
            candidate.write_text(text)
            output = root / "release"
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--artifact-root", str(root),
                "--output-dir", str(output),
                "--product-commit", commits["product"],
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            files = {path.name for path in output.iterdir()}
            self.assertTrue(any(name.endswith(".ota.tar") for name in files))
            manifest = json.loads(next(output.glob("*-build.json")).read_text())
            self.assertTrue(manifest["signed"])
            self.assertTrue(manifest["ota_bundle"])

    def test_preserves_enabled_ssh_identity_in_release_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, commits = fixture(root)
            candidate = run / "CURRENT.candidate"
            text = candidate.read_text()
            text = text.replace(
                "ssh_enabled=0\n",
                "ssh_enabled=1\ndropbear_sha256=" + "a" * 64 +
                "\ndropbearkey_sha256=" + "b" * 64 + "\n",
            )
            candidate.write_text(text)
            manifest_path = run / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["ssh"] = {
                "enabled": True,
                "files": {
                    "sbin/dropbear": {"sha256": "a" * 64},
                    "sbin/dropbearkey": {"sha256": "b" * 64},
                },
            }
            manifest_path.write_text(json.dumps(manifest))
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--artifact-root", str(root),
                "--output-dir", str(root / "release"),
                "--product-commit", commits["product"],
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            release_manifest = json.loads(next((root / "release").glob("*-build.json")).read_text())
            self.assertTrue(release_manifest["ssh_enabled"])
            self.assertEqual(release_manifest["ssh"]["dropbear_sha256"], "a" * 64)

    def test_rejects_wrong_triggering_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--artifact-root", str(root),
                "--output-dir", str(root / "release"),
                "--product-commit", "9" * 40,
            ], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match triggering workflow", result.stderr)


if __name__ == "__main__":
    unittest.main()
