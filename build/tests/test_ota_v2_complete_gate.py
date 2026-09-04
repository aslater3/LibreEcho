#!/usr/bin/env python3
"""Behavioral gates for the complete Product OTA v2 contract."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from nacl.signing import SigningKey

CI = Path(__file__).resolve().parents[1] / "ci"
ROOT = CI.parents[1]
sys.path.insert(0, str(CI))
from ota_v2_product import ContractError, FEATURES, validate_control_tar  # noqa: E402

PUBLISHER = ROOT / "build/ci/publish-stable-release.sh"
PLATFORM = ROOT.parent / "platform/tools/mt8163-arm32/ota/make_ota_bundle.py"
DAEMONS = {
    "airplay2": "usr/local/sbin/libreecho-audio-engine",
    "tts": "usr/local/sbin/libreecho-ttsd",
    "wakeword": "usr/local/sbin/libreecho-waked",
    "stt": "usr/local/sbin/libreecho-sttd",
    "assistant": "usr/local/sbin/libreecho-agentd",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contract(root: Path) -> tuple[dict[str, object], dict[str, object], Path, Path, SigningKey]:
    release = "0.13.11"
    source = "4" * 40
    asset_dir = root / "ota-assets"
    asset_dir.mkdir()
    records: list[dict[str, object]] = []
    for feature in FEATURES:
        record: dict[str, object] = {
            "feature_id": feature, "action": "preserve", "activation": "reboot",
            "base_payload_sha256": "a" * 64, "base_manifest_sha256": "b" * 64,
            "daemon_path": DAEMONS[feature], "daemon_sha256": "c" * 64,
            "release": release, "source_commit": source,
        }
        if feature == "assistant":
            payload = b"assistant replacement payload"
            manifest = b'{"feature_id":"assistant"}\n'
            payload_name = f"libreecho-radar-puffin-{release}-assistant.payload.squashfs"
            manifest_name = f"libreecho-radar-puffin-{release}-assistant.manifest.json"
            (asset_dir / payload_name).write_bytes(payload)
            (asset_dir / manifest_name).write_bytes(manifest)
            record.update({
                "action": "replace", "asset": payload_name, "size": len(payload), "sha256": sha256(payload),
                "manifest_asset": manifest_name, "manifest_size": len(manifest), "manifest_sha256": sha256(manifest),
            })
        records.append(record)
    plan = {
        "schema": "libreecho-product-feature-plan-v1", "transaction_type": "system",
        "activation": "reboot", "release": release, "source_commit": source, "features": records,
    }
    assets = [
        {"feature_id": "assistant", "action": "replace", "kind": "manifest",
         "name": records[-1]["manifest_asset"], "size": records[-1]["manifest_size"], "sha256": records[-1]["manifest_sha256"]},
        {"feature_id": "assistant", "action": "replace", "kind": "payload",
         "name": records[-1]["asset"], "size": records[-1]["size"], "sha256": records[-1]["sha256"]},
    ]
    inventory = {
        "schema": "libreecho-product-feature-assets-v1", "transaction_type": "system",
        "activation": "reboot", "release": release, "source_commit": source, "assets": assets,
    }
    boot = root / "boot.img"
    boot.write_bytes(b"ANDROID!" + bytes(16 * 1024 * 1024 - 8))
    key = SigningKey.generate()
    public = root / "public.hex"
    public.write_text(key.verify_key.encode().hex() + "\n")
    return plan, inventory, asset_dir, boot, key


def control_raw(plan: dict[str, object], boot: Path, *, records: list[dict[str, object]] | None = None,
                feature_ids: str | None = None, service_profile: str = "production",
                feature_policy: str = "community-noncommercial", extra: str = "") -> bytes:
    selected = list(records if records is not None else plan["features"])  # type: ignore[arg-type]
    release = plan["release"]
    lines: list[str] = [
        "format=libreecho-ota-v2", "manifest_version=1", "board=radar_puffin", "soc=mt8163",
        "architecture=armv7", "image_profile=ota", "transaction_type=system", "transaction_id=txn-" + "1" * 24,
        f"version={release}", "update_channel=stable", f"service_profile={service_profile}",
        f"feature_policy={feature_policy}", "minimum_updater_schema=2",
        "feature_asset_base=github-release-channel", "commit_policy=after-slot-confirm",
        "boot_filename=boot.img", f"boot_size={boot.stat().st_size}",
        f"boot_sha256={sha256(boot.read_bytes())}",
        f"feature_ids={feature_ids if feature_ids is not None else ','.join(FEATURES)}",
    ]
    for record in selected:
        prefix = f"feature_{record['feature_id']}_"
        for field in ("action", "activation", "base_payload_sha256", "base_manifest_sha256", "daemon_path", "daemon_sha256", "release", "source_commit"):
            if field in record:
                lines.append(prefix + field + "=" + str(record[field]))
        if record.get("action") != "preserve":
            for field in ("asset", "size", "sha256", "manifest_asset", "manifest_size", "manifest_sha256"):
                if field in record:
                    lines.append(prefix + field + "=" + str(record[field]))
    return ("\n".join(lines) + "\n" + extra).encode("ascii")


def write_control(path: Path, raw: bytes, key: SigningKey, boot: Path) -> None:
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in (("manifest", raw), ("manifest.sig", key.sign(raw).signature.hex().encode() + b"\n"), ("boot.img", boot.read_bytes())):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.uid = info.gid = 0
            archive.addfile(info, __import__("io").BytesIO(data))


class CompleteControlGateTests(unittest.TestCase):
    def assert_rejected(self, root: Path, plan: dict[str, object], inventory: dict[str, object], asset_dir: Path,
                        boot: Path, key: SigningKey, raw: bytes) -> None:
        bundle = root / "candidate.ota.tar"
        write_control(bundle, raw, key, boot)
        with self.assertRaises(ContractError):
            validate_control_tar(
                bundle, root / "public.hex", "v2", "0.13.11",
                feature_plan=plan, feature_inventory=inventory, feature_asset_dir=asset_dir,
                expected_channel="stable", boot_path=boot,
                expected_key_sha256=sha256((root / "public.hex").read_bytes()),
            )

    def test_signed_all_five_ids_without_records_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-v2-malformed-ids-") as directory:
            root = Path(directory)
            plan, inventory, assets, boot, key = contract(root)
            self.assert_rejected(root, plan, inventory, assets, boot, key, control_raw(plan, boot, records=[]))

    def test_signed_missing_record_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-v2-missing-record-") as directory:
            root = Path(directory)
            plan, inventory, assets, boot, key = contract(root)
            self.assert_rejected(root, plan, inventory, assets, boot, key, control_raw(plan, boot, records=plan["features"][:-1]))

    def test_signed_extra_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-v2-extra-key-") as directory:
            root = Path(directory)
            plan, inventory, assets, boot, key = contract(root)
            self.assert_rejected(root, plan, inventory, assets, boot, key, control_raw(plan, boot, extra="feature_assistant_extra=1\n"))

    def test_signed_action_and_hash_mismatch_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-v2-record-mismatch-") as directory:
            root = Path(directory)
            plan, inventory, assets, boot, key = contract(root)
            action_mismatch = json.loads(json.dumps(plan["features"]))
            action_mismatch[-1]["action"] = "runtime"
            action_mismatch[-1]["asset"] = action_mismatch[-1]["asset"].replace(".payload.", ".runtime.")
            action_mismatch[-1]["manifest_asset"] = action_mismatch[-1]["manifest_asset"].replace(".manifest.", ".runtime-manifest.")
            self.assert_rejected(root, plan, inventory, assets, boot, key, control_raw(plan, boot, records=action_mismatch))
            hash_mismatch = json.loads(json.dumps(plan["features"]))
            hash_mismatch[-1]["sha256"] = "d" * 64
            self.assert_rejected(root, plan, inventory, assets, boot, key, control_raw(plan, boot, records=hash_mismatch))

    def test_signed_invalid_profile_and_policy_tokens_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-v2-policy-token-") as directory:
            root = Path(directory)
            plan, inventory, assets, boot, key = contract(root)
            for kwargs in (
                {"service_profile": "not-a-profile"},
                {"feature_policy": "not-a-policy"},
                {"service_profile": "diagnostic", "feature_policy": "redistributable"},
            ):
                with self.subTest(kwargs=kwargs):
                    self.assert_rejected(
                        root, plan, inventory, assets, boot, key,
                        control_raw(
                            plan, boot,
                            service_profile=kwargs.get("service_profile", "production"),
                            feature_policy=kwargs.get("feature_policy", "community-noncommercial"),
                        ),
                    )

    def test_plan_control_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-v2-plan-mismatch-") as directory:
            root = Path(directory)
            plan, inventory, assets, boot, key = contract(root)
            changed = json.loads(json.dumps(plan))
            changed["features"][0]["daemon_sha256"] = "d" * 64
            self.assert_rejected(root, changed, inventory, assets, boot, key, control_raw(plan, boot))


class StablePublisherPreMutationTests(unittest.TestCase):
    def _prepared_release(self, root: Path) -> Path:
        from build.tests.test_release_packaging import add_v2_contract, fixture
        artifact, product = fixture(root)
        add_v2_contract(artifact)
        output = root / "release"
        result = subprocess.run([
            sys.executable, str(ROOT / "build/ci/prepare-stable-release.py"),
            "--artifact-root", str(artifact), "--product-root", str(product), "--product-commit", "1" * 40,
            "--release-version", "0.14.0", "--release-notes", "release/radar-puffin-v0.14.0.md",
            "--amonet-repository", "https://github.com/aslater3/amonet-k32", "--amonet-tag", "v1.0.0",
            "--amonet-commit", "dfefe52f0eed7296012707cfff1f753b0ea33257", "--output-dir", str(output),
        ], env={**os.environ, "LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256": sha256((artifact / "run" / "ota-public-key.hex").read_bytes())}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return output

    def _run_publisher(self, output: Path, root: Path, anchor: str | None = None) -> subprocess.CompletedProcess[str]:
        fake = root / "fake-bin"
        fake.mkdir(parents=True, exist_ok=True)
        log = root / "gh.log"
        (fake / "gh").write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >>\"$GH_LOG\"\nexit 99\n")
        (fake / "gh").chmod(0o755)
        env = dict(os.environ)
        env.update({"PATH": f"{fake}:{env['PATH']}", "GH_LOG": str(log), "PUBLISH_DRY_RUN": "1",
                    "LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256": anchor or sha256(next(output.glob("*-ota-public-key.hex")).read_bytes()),
                    "RELEASE_TAG": "radar-puffin-v0.14.0", "RELEASE_DIR": str(output),
                    "RELEASE_NOTES": "release-notes.md", "HEAD_SHA": "1" * 40})
        return subprocess.run([str(PUBLISHER)], cwd=ROOT, env=env, capture_output=True, text=True)

    def test_replaced_key_resigned_and_rehashed_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-v2-key-anchor-") as directory:
            root = Path(directory)
            output = self._prepared_release(root / "valid")
            key_path = next(output.glob("*-ota-public-key.hex"))
            original_anchor = sha256(key_path.read_bytes())
            ota_path = output / "libreecho-radar-puffin-v0.14.0.ota.tar"
            with tarfile.open(ota_path, "r") as archive:
                raw = archive.extractfile("manifest").read()
                boot = root / "boot.img"
                boot.write_bytes(archive.extractfile("boot.img").read())
            replacement = SigningKey.generate()
            write_control(ota_path, raw, replacement, boot)
            shutil.copyfile(ota_path, output / "libreecho-radar-puffin-stable.ota.tar")
            key_path.write_text(replacement.verify_key.encode().hex() + "\n")
            sums = next(output.glob("*-SHA256SUMS"))
            sums.write_text("".join(
                f"{sha256(path.read_bytes())}  {path.name}\n"
                for path in sorted(output.iterdir()) if path != sums
            ), encoding="ascii")
            result = self._run_publisher(output, root / "run", original_anchor)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("trusted digest anchor", result.stderr)
            self.assertFalse((root / "run" / "gh.log").exists())

        with tempfile.TemporaryDirectory(prefix="ota-v2-publisher-behavior-") as directory:
            root = Path(directory)
            valid = self._prepared_release(root / "valid")
            result = self._run_publisher(valid, root / "valid-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PREPARED_NOT_PUBLISHED", result.stdout)
            self.assertFalse((root / "valid-run" / "gh.log").exists())
            for name, mutate in (
                ("missing", lambda out: (out / "libreecho-radar-puffin-0.14.0-assistant.runtime.squashfs").unlink()),
                ("extra", lambda out: (out / "unexpected.bin").write_bytes(b"extra")),
                ("tampered", lambda out: (out / "libreecho-radar-puffin-0.14.0-assistant.runtime.squashfs").write_bytes(b"tampered")),
                ("partial", lambda out: json_dump_remove_asset(out)),
            ):
                case_root = root / name
                case_root.mkdir()
                output = shutil.copytree(valid, case_root / "release")
                mutate(output)
                failed = self._run_publisher(output, case_root / "run")
                self.assertNotEqual(failed.returncode, 0, name)
                log = case_root / "run" / "gh.log"
                self.assertFalse(log.exists() and log.read_text().strip(), name)


def json_dump_remove_asset(output: Path) -> None:
    path = next(output.glob("*-feature-assets.json"))
    value = json.loads(path.read_text())
    value["assets"] = value["assets"][:-1]
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    unittest.main()
