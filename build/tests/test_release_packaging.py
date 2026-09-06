#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from nacl.signing import SigningKey

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "build/ci/prepare-stable-release.py"
PLATFORM_TOOL = Path(os.environ.get(
    "LIBREECHO_PLATFORM_SRC",
    str(ROOT.parent / "platform"),
)) / "tools/mt8163-arm32/ota/make_ota_bundle.py"
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
    boot.write_bytes(b"ANDROID!" + bytes(16 * 1024 * 1024 - 8))
    ota = run / "libreecho-run.ota.tar"
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
        "ota_bundle_sha256": "",
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
        "output": {"sha256": digest(boot), "size": boot.stat().st_size},
        "image_profile": "ota", "service_profile": "production",
        "feature_policy": "community-noncommercial", "update_channel": "stable",
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
    signing = SigningKey.generate()
    (run / "fixture-signing-key.hex").write_text(signing.encode().hex() + "\n")
    (run / "ota-public-key.hex").write_text(signing.verify_key.encode().hex() + "\n")
    built = subprocess.run([
        sys.executable, str(PLATFORM_TOOL), "--format", "v1", "--boot-image", str(boot),
        "--build-manifest", str(run / "manifest.json"), "--version", "fixture-v1",
        "--signing-key", str(run / "fixture-signing-key.hex"),
        "--public-key", str(run / "ota-public-key.hex"), "--service-profile", "production",
        "--feature-policy", "community-noncommercial", "--update-channel", "stable",
        "--output", str(ota),
    ], text=True, capture_output=True)
    if built.returncode != 0:
        raise RuntimeError(built.stderr)
    candidate["ota_bundle_sha256"] = digest(ota)
    (run / "CURRENT.candidate").write_text(
        "".join(f"{key}={value}\n" for key, value in candidate.items())
    )
    return root / "artifact", product


def add_v2_contract(artifact_root: Path, release: str = "0.14.0") -> dict[str, str]:
    run = artifact_root / "run"
    asset_dir = run / "ota-assets"
    asset_dir.mkdir()
    payload_name = f"libreecho-radar-puffin-{release}-assistant.runtime.squashfs"
    manifest_name = f"libreecho-radar-puffin-{release}-assistant.runtime-manifest.json"
    payload = asset_dir / payload_name
    manifest = asset_dir / manifest_name
    payload.write_bytes(b"assistant runtime capsule")
    manifest.write_text("{\"feature_id\":\"assistant\"}\n")
    daemon_paths = {
        "airplay2": "usr/local/sbin/libreecho-audio-engine",
        "tts": "usr/local/sbin/libreecho-ttsd",
        "wakeword": "usr/local/sbin/libreecho-waked",
        "stt": "usr/local/sbin/libreecho-sttd",
        "assistant": "usr/local/sbin/libreecho-agentd",
    }
    records = []
    for feature in FEATURES:
        record: dict[str, object] = {
            "feature_id": feature, "action": "preserve" if feature != "assistant" else "runtime",
            "activation": "reboot", "base_payload_sha256": "a" * 64,
            "base_manifest_sha256": "b" * 64, "daemon_path": daemon_paths[feature],
            "daemon_sha256": "c" * 64, "release": release,
            "source_commit": "4" * 40,
        }
        if feature == "assistant":
            record.update({
                "asset": payload_name, "size": payload.stat().st_size, "sha256": digest(payload),
                "manifest_asset": manifest_name, "manifest_size": manifest.stat().st_size,
                "manifest_sha256": digest(manifest),
            })
        records.append(record)
    (run / "feature-plan.json").write_text(json.dumps({
        "schema": "libreecho-product-feature-plan-v1", "transaction_type": "system",
        "activation": "reboot", "release": release, "source_commit": "4" * 40,
        "features": records,
    }) + "\n")
    (run / "feature-assets.json").write_text(json.dumps({
        "schema": "libreecho-product-feature-assets-v1", "transaction_type": "system",
        "activation": "reboot", "release": release, "source_commit": "4" * 40,
        "assets": [
            {"feature_id": "assistant", "action": "runtime", "kind": "manifest", "name": manifest_name, "size": manifest.stat().st_size, "sha256": digest(manifest)},
            {"feature_id": "assistant", "action": "runtime", "kind": "payload", "name": payload_name, "size": payload.stat().st_size, "sha256": digest(payload)},
        ],
    }) + "\n")
    candidate = run / "CURRENT.candidate"
    candidate.write_text(candidate.read_text() + (
        "ota_format=v2\n"
        f"ota_release={release}\n"
        f"feature_plan={run / 'feature-plan.json'}\n"
        f"feature_asset_inventory={run / 'feature-assets.json'}\n"
        f"feature_asset_dir={asset_dir}\n"
    ))
    ota = run / "libreecho-run.ota.tar"
    ota.unlink()
    built = subprocess.run([
        sys.executable, str(PLATFORM_TOOL), "--format", "v2", "--boot-image", str(run / "boot.img"),
        "--build-manifest", str(run / "manifest.json"), "--feature-plan", str(run / "feature-plan.json"),
        "--version", release, "--signing-key", str(run / "fixture-signing-key.hex"),
        "--public-key", str(run / "ota-public-key.hex"), "--service-profile", "production",
        "--feature-policy", "community-noncommercial", "--update-channel", "stable",
        "--output", str(run / "libreecho-run.ota.tar"),
    ], text=True, capture_output=True)
    if built.returncode != 0:
        raise RuntimeError(built.stderr)
    candidate = run / "CURRENT.candidate"
    candidate.write_text(candidate.read_text() + f"ota_bundle_sha256={digest(run / 'libreecho-run.ota.tar')}\n")
    return {"payload": payload_name, "manifest": manifest_name}


class StableReleasePackagingTests(unittest.TestCase):
    def test_stable_packager_stages_v2_external_assets_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_root, product = fixture(root)
            names = add_v2_contract(artifact_root)
            result = subprocess.run([
                sys.executable, str(SCRIPT), "--artifact-root", str(artifact_root),
                "--product-root", str(product), "--product-commit", "1" * 40,
                "--release-version", "0.14.0", "--release-notes", "release/radar-puffin-v0.14.0.md",
                "--amonet-repository", "https://github.com/aslater3/amonet-k32", "--amonet-tag", "v1.0.0",
                "--amonet-commit", WORKING_AMONET_COMMIT, "--output-dir", str(root / "release"),
            ], env={**os.environ, "LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256": digest(artifact_root / "run" / "ota-public-key.hex")}, text=True, capture_output=True)
            output = root / "release"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / names["payload"]).is_file())
            self.assertTrue((output / names["manifest"]).is_file())
            release_manifest = json.loads(next(output.glob("*-build.json")).read_text())
            self.assertEqual({item["name"] for item in release_manifest["feature_assets"]}, set(names.values()))

    def test_stable_packager_rejects_tampered_v2_external_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact_root, product = fixture(root)
            names = add_v2_contract(artifact_root)
            (artifact_root / "run" / "ota-assets" / names["payload"]).write_bytes(b"tampered")
            result = subprocess.run([
                sys.executable, str(SCRIPT), "--artifact-root", str(artifact_root),
                "--product-root", str(product), "--product-commit", "1" * 40,
                "--release-version", "0.14.0", "--release-notes", "release/radar-puffin-v0.14.0.md",
                "--amonet-repository", "https://github.com/aslater3/amonet-k32", "--amonet-tag", "v1.0.0",
                "--amonet-commit", WORKING_AMONET_COMMIT, "--output-dir", str(root / "release"),
            ], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
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
