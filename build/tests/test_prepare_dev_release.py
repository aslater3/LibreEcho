#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from nacl.signing import SigningKey

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "build/ci/prepare-dev-release.py"
PLATFORM_TOOL = Path(os.environ.get(
    "LIBREECHO_PLATFORM_SRC",
    str(ROOT.parent / "platform"),
)) / "tools/mt8163-arm32/ota/make_ota_bundle.py"
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
    boot.write_bytes(b"ANDROID!" + bytes(16 * 1024 * 1024 - 8))
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
        "output": {"sha256": digest(boot), "size": boot.stat().st_size},
        "image_profile": "ota", "service_profile": "production",
        "feature_policy": "community-noncommercial", "update_channel": "dev",
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


def make_signed_ota(run: Path, output: Path, version: str = "fixture-v1", ota_format: str = "v1", plan: Path | None = None) -> None:
    signing = SigningKey.generate()
    signing_path = run / "fixture-signing-key.hex"
    public_path = run / "ota-public-key.hex"
    signing_path.write_text(signing.encode().hex() + "\n")
    public_path.write_text(signing.verify_key.encode().hex() + "\n")
    build = run / "manifest.json"
    manifest = json.loads(build.read_text())
    manifest.update({"output": {"sha256": digest(run / "boot.img"), "size": (run / "boot.img").stat().st_size},
                    "image_profile": "ota", "service_profile": "production",
                    "feature_policy": "community-noncommercial", "update_channel": "dev"})
    build.write_text(json.dumps(manifest))
    command = [sys.executable, str(PLATFORM_TOOL), "--format", ota_format,
               "--boot-image", str(run / "boot.img"), "--build-manifest", str(build),
               "--version", version, "--signing-key", str(signing_path),
               "--public-key", str(public_path), "--service-profile", "production",
               "--feature-policy", "community-noncommercial", "--update-channel", "dev"]
    if plan is not None:
        command.extend(("--feature-plan", str(plan)))
    command.extend(("--output", str(output),))
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


def add_v2_contract(run: Path, commits: dict[str, str], release: str = "0.13.11") -> dict[str, str]:
    asset_dir = run / "ota-assets"
    asset_dir.mkdir()
    payload_name = f"libreecho-radar-puffin-{release}-assistant.runtime.squashfs"
    manifest_name = f"libreecho-radar-puffin-{release}-assistant.runtime-manifest.json"
    payload = asset_dir / payload_name
    manifest = asset_dir / manifest_name
    payload.write_bytes(b"assistant runtime capsule")
    manifest.write_text("{\"feature_id\":\"assistant\"}\n")
    records = []
    for feature in FEATURES:
        common: dict[str, object] = {
            "feature_id": feature,
            "activation": "reboot",
            "base_payload_sha256": "a" * 64,
            "base_manifest_sha256": "b" * 64,
            "daemon_path": {
                "airplay2": "usr/local/sbin/libreecho-audio-engine",
                "tts": "usr/local/sbin/libreecho-ttsd",
                "wakeword": "usr/local/sbin/libreecho-waked",
                "stt": "usr/local/sbin/libreecho-sttd",
                "assistant": "usr/local/sbin/libreecho-agentd",
            }[feature],
            "daemon_sha256": "c" * 64,
            "release": release,
            "source_commit": commits["ui"],
        }
        if feature == "assistant":
            common.update({
                "action": "runtime",
                "asset": payload_name,
                "size": payload.stat().st_size,
                "sha256": digest(payload),
                "manifest_asset": manifest_name,
                "manifest_size": manifest.stat().st_size,
                "manifest_sha256": digest(manifest),
            })
        else:
            common["action"] = "preserve"
        records.append(common)
    plan = run / "feature-plan.json"
    inventory = run / "feature-assets.json"
    plan.write_text(json.dumps({
        "schema": "libreecho-product-feature-plan-v1",
        "transaction_type": "system",
        "activation": "reboot",
        "release": release,
        "source_commit": commits["ui"],
        "features": records,
    }) + "\n")
    inventory.write_text(json.dumps({
        "schema": "libreecho-product-feature-assets-v1",
        "transaction_type": "system",
        "activation": "reboot",
        "release": release,
        "source_commit": commits["ui"],
        "assets": [
            {"feature_id": "assistant", "action": "runtime", "kind": "manifest", "name": manifest_name, "size": manifest.stat().st_size, "sha256": digest(manifest)},
            {"feature_id": "assistant", "action": "runtime", "kind": "payload", "name": payload_name, "size": payload.stat().st_size, "sha256": digest(payload)},
        ],
    }) + "\n")
    candidate = run / "CURRENT.candidate"
    text = candidate.read_text()
    text += (
        "ota_format=v2\n"
        f"ota_release={release}\n"
        f"feature_plan={plan}\n"
        f"feature_asset_inventory={inventory}\n"
        f"feature_asset_dir={asset_dir}\n"
    )
    candidate.write_text(text)
    return {"payload": payload_name, "manifest": manifest_name}


class Tests(unittest.TestCase):
    def test_signed_all_preserve_publication_without_empty_asset_directory(self):
        import shutil
        for shape in ('missing', 'symlink', 'file'):
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run, commits = fixture(root)
                add_v2_contract(run, commits)
                plan_path = run / 'feature-plan.json'
                plan = json.loads(plan_path.read_text())
                for record in plan['features']:
                    record['action'] = 'preserve'
                    for key in ('asset', 'size', 'sha256', 'manifest_asset', 'manifest_size', 'manifest_sha256'):
                        record.pop(key, None)
                plan_path.write_text(json.dumps(plan))
                inventory_path = run / 'feature-assets.json'
                inventory = json.loads(inventory_path.read_text())
                inventory['assets'] = []
                inventory_path.write_text(json.dumps(inventory))
                ota = run / 'development.ota.tar'
                make_signed_ota(run, ota, '0.13.11', 'v2', plan_path)
                candidate = run / 'CURRENT.candidate'
                text = candidate.read_text().replace('ota_signing_mode=github\n', 'ota_signing_mode=local\n')
                text = text.replace('ota_bundle=\n', 'ota_bundle=' + str(ota) + '\n')
                text = text.replace('ota_bundle_sha256=\n', 'ota_bundle_sha256=' + digest(ota) + '\n')
                candidate.write_text(text)
                shutil.rmtree(run / 'ota-assets')
                if shape == 'symlink':
                    (run / 'ota-assets').symlink_to(root / 'absent')
                elif shape == 'file':
                    (run / 'ota-assets').write_text('not a directory')
                result = subprocess.run([
                    sys.executable, str(SCRIPT), '--artifact-root', str(root),
                    '--output-dir', str(root / 'release'), '--product-commit', commits['product'],
                ], env={**os.environ, 'LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256': digest(run / 'ota-public-key.hex')}, capture_output=True, text=True, timeout=30)
                if shape == 'missing':
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(list((root / 'release').glob('*.ota.tar')))
                else:
                    self.assertNotEqual(result.returncode, 0)

    def test_prepares_v2_external_assets_without_renaming_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, commits = fixture(root)
            names = add_v2_contract(run, commits)
            ota = run / "development.ota.tar"
            make_signed_ota(run, ota, "0.13.11", "v2", run / "feature-plan.json")
            candidate = run / "CURRENT.candidate"
            text = candidate.read_text().replace("ota_signing_mode=github\n", "ota_signing_mode=local\n")
            text = text.replace("ota_bundle=\n", "ota_bundle=" + str(ota) + "\n")
            text = text.replace("ota_bundle_sha256=\n", "ota_bundle_sha256=" + digest(ota) + "\n")
            candidate.write_text(text)
            output = root / "release"
            result = subprocess.run([
                sys.executable, str(SCRIPT), "--artifact-root", str(root),
                "--output-dir", str(output), "--product-commit", commits["product"],
            ], env={**os.environ, "LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256": digest(run / "ota-public-key.hex")}, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output / names["payload"]).is_file())
            self.assertTrue((output / names["manifest"]).is_file())
            self.assertIn(names["payload"], (next(output.glob("*-SHA256SUMS"))).read_text())
            release_manifest = json.loads(next(output.glob("*-build.json")).read_text())
            self.assertEqual({item["name"] for item in release_manifest["feature_assets"]}, set(names.values()))

    def test_rejects_invalid_v2_feature_asset_inventory(self) -> None:
        mutations = ("missing", "tampered", "mismatched", "duplicate", "unsafe", "partial")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run, commits = fixture(root)
                names = add_v2_contract(run, commits)
                inventory = run / "feature-assets.json"
                data = json.loads(inventory.read_text())
                if mutation == "missing":
                    (run / "ota-assets" / names["payload"]).unlink()
                elif mutation == "tampered":
                    (run / "ota-assets" / names["payload"]).write_bytes(b"tampered")
                elif mutation == "mismatched":
                    data["assets"][0]["sha256"] = "d" * 64
                    inventory.write_text(json.dumps(data))
                elif mutation == "duplicate":
                    data["assets"].append(dict(data["assets"][0]))
                    inventory.write_text(json.dumps(data))
                elif mutation == "unsafe":
                    data["assets"][0]["name"] = "../unsafe.runtime.squashfs"
                    inventory.write_text(json.dumps(data))
                else:
                    data["assets"] = data["assets"][:1]
                    inventory.write_text(json.dumps(data))
                result = subprocess.run([
                    sys.executable, str(SCRIPT), "--artifact-root", str(root),
                    "--output-dir", str(root / "release"),
                    "--product-commit", commits["product"],
                ], text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0, mutation)
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
            run, commits = fixture(root)
            ota = run / "nightly.ota.tar"
            make_signed_ota(run, ota)
            candidate = run / "CURRENT.candidate"
            text = candidate.read_text()
            text = text.replace("ota_signing_mode=github\n", "ota_signing_mode=local\n")
            text = text.replace("ota_bundle=\n", "ota_bundle=" + str(ota) + "\n")
            text = text.replace("ota_bundle_sha256=\n", "ota_bundle_sha256=" + digest(ota) + "\n")
            candidate.write_text(text)
            output = root / "release"
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--artifact-root", str(root),
                "--output-dir", str(output),
                "--product-commit", commits["product"],
                "--release-kind", "nightly",
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            files = {path.name for path in output.iterdir()}
            self.assertEqual(len(files), 20)
            self.assertIn("-initial-install.tar", next(name for name in files if name.endswith("-initial-install.tar")))
            self.assertTrue(any(name.endswith("-installer.py") for name in files))
            self.assertIn("asset_count=20", result.stdout)

    def test_prepares_bounded_development_initial_install_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, commits = fixture(root)
            ota = run / "development.ota.tar"
            make_signed_ota(run, ota)
            candidate = run / "CURRENT.candidate"
            text = candidate.read_text()
            text = text.replace("ota_signing_mode=github\n", "ota_signing_mode=local\n")
            text = text.replace("ota_bundle=\n", "ota_bundle=" + str(ota) + "\n")
            text = text.replace("ota_bundle_sha256=\n", "ota_bundle_sha256=" + digest(ota) + "\n")
            candidate.write_text(text)
            output = root / "release"
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--artifact-root", str(root),
                "--output-dir", str(output),
                "--product-commit", commits["product"],
                "--release-kind", "development",
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            files = {path.name for path in output.iterdir()}
            self.assertEqual(len(files), 20)
            self.assertTrue(any(name.startswith("libreecho-radar-puffin-build-") and name.endswith("-initial-install.tar") for name in files))
            self.assertTrue(any(name.startswith("libreecho-radar-puffin-build-") and name.endswith("-installer.py") for name in files))
            self.assertIn("asset_count=20", result.stdout)

    def test_prepares_signed_dev_release_with_ota_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, commits = fixture(root)
            ota = run / "signed.ota.tar"
            make_signed_ota(run, ota)
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
