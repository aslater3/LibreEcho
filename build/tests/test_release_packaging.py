#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "build/ci/prepare-stable-release.py"
WORKING_AMONET_COMMIT = "dfefe52f0eed7296012707cfff1f753b0ea33257"
FEATURES = ("airplay2", "tts", "wakeword", "stt", "assistant")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(root: Path) -> tuple[Path, Path]:
    run = root / "artifact" / "run"
    (run / "features").mkdir(parents=True)
    product = root / "product"
    (product / "release").mkdir(parents=True)
    (product / "tools").mkdir(parents=True)
    (product / "tools/libreecho-install.py").write_text("#!/usr/bin/env python3\n")
    (product / "tools/run-one-shot.sh").write_text("#!/usr/bin/env bash\n")
    (product / "release/radar-puffin-v0.14.0.md").write_text("# LibreEcho v0.14.0\n")

    boot = run / "boot.img"
    boot.write_bytes(b"boot")
    ota = run / "libreecho-run.ota.tar"
    ota.write_bytes(b"signed ota")
    key = run / "ota-public-key.hex"
    key.write_text("a" * 64 + "\n")
    candidate = {
        "status": "PREPARED_NOT_FLASHED",
        "public_release_mode": "1",
        "update_channel": "stable",
        "image_profile": "ota",
        "service_profile": "production",
        "feature_policy": "community-noncommercial",
        "ssh_enabled": "0",
        "ota_signing_mode": "local",
        "ota_bundle": str(ota),
        "ota_bundle_sha256": digest(ota),
        "boot_image_sha256": digest(boot),
        "product_git_head": "1" * 40,
        "tooling_git_head": "2" * 40,
        "ui_commit": "4" * 40,
        "product_git_diff_sha256": hashlib.sha256(b"").hexdigest(),
        "tooling_git_diff_sha256": hashlib.sha256(b"").hexdigest(),
        "kernel_git_diff_sha256": hashlib.sha256(b"").hexdigest(),
        "ui_diff_sha256": hashlib.sha256(b"").hexdigest(),
    }
    for feature in FEATURES:
        key_name = "airplay" if feature == "airplay2" else feature
        payload = run / "features" / f"{feature}.squashfs"
        manifest = run / "features" / f"{feature}.manifest.json"
        payload.write_bytes((feature + " payload").encode())
        manifest.write_text(json.dumps({"feature": feature}) + "\n")
        candidate[f"{key_name}_payload_sha256"] = digest(payload)
        candidate[f"{key_name}_payload_size"] = str(payload.stat().st_size)
        candidate[f"{key_name}_feature_manifest_sha256"] = digest(manifest)
    (run / "CURRENT.candidate").write_text(
        "".join(f"{key}={value}\n" for key, value in candidate.items())
    )
    (run / "provenance.txt").write_text("kernel_git_head=" + "3" * 40 + "\n")
    (run / "manifest.json").write_text(json.dumps({
        "connectivity": {
            "embedded_vendor_file_count": 0,
            "vendor_delivery": "owner-device-local-extraction",
        }
    }))
    (run / "release-source-commits.txt").write_text(
        "product=" + "1" * 40 + "\n"
        "platform=" + "2" * 40 + "\n"
        "linux=" + "3" * 40 + "\n"
        "ui=" + "4" * 40 + "\n"
    )
    (run / "verify.log").write_text(
        "arm32_recovery_image_contract=PASS status=PREPARED_NOT_FLASHED\n"
    )
    (run / "release-request.json").write_text(json.dumps({
        "schema": "libreecho-release-request-v1",
        "channel": "stable",
        "version": "0.14.0",
        "release_tag": "radar-puffin-v0.14.0",
        "release_notes": "release/radar-puffin-v0.14.0.md",
    }))
    return root / "artifact", product


class StableReleasePackagingTests(unittest.TestCase):
    def test_stable_packager_requires_and_publishes_signed_ota_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_root, product = fixture(root)
            output = root / "release"
            result = subprocess.run([
                sys.executable,
                str(SCRIPT),
                "--artifact-root", str(artifact_root),
                "--product-root", str(product),
                "--product-commit", "1" * 40,
                "--release-version", "0.14.0",
                "--release-notes", "release/radar-puffin-v0.14.0.md",
                "--amonet-repository", "https://github.com/aslater3/amonet-k32",
                "--amonet-tag", "v1.0.0",
                "--amonet-commit", WORKING_AMONET_COMMIT,
                "--output-dir", str(output),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            prefix = "libreecho-radar-puffin-v0.14.0"
            self.assertTrue((output / f"{prefix}.ota.tar").is_file())
            self.assertTrue((output / f"{prefix}-initial-install.tar").is_file())
            self.assertTrue((output / f"{prefix}-installer.py").is_file())
            self.assertTrue((output / f"{prefix}-run-one-shot.sh").is_file())
            sums = output / f"{prefix}-SHA256SUMS"
            self.assertEqual(
                set(line.split("  ", 1)[1] for line in sums.read_text().splitlines()),
                {path.name for path in output.iterdir() if path.name != sums.name},
            )
            with tarfile.open(output / f"{prefix}-initial-install.tar") as archive:
                manifest = json.load(archive.extractfile("manifest.json"))
            self.assertEqual(manifest["release"], "radar-puffin-v0.14.0")
            self.assertEqual(manifest["amonet"]["commit"], WORKING_AMONET_COMMIT)

    def test_stable_packager_rejects_missing_ota(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_root, product = fixture(root)
            next(artifact_root.rglob("*.ota.tar")).unlink()
            result = subprocess.run([
                sys.executable,
                str(SCRIPT),
                "--artifact-root", str(artifact_root),
                "--product-root", str(product),
                "--product-commit", "1" * 40,
                "--release-version", "0.14.0",
                "--release-notes", "release/radar-puffin-v0.14.0.md",
                "--amonet-repository", "https://github.com/aslater3/amonet-k32",
                "--amonet-tag", "v1.0.0",
                "--amonet-commit", WORKING_AMONET_COMMIT,
                "--output-dir", str(root / "release"),
            ], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly one signed OTA bundle", result.stderr)


if __name__ == "__main__":
    unittest.main()
