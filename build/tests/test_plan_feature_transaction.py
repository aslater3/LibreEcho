#!/usr/bin/env python3
"""Host contract tests for Product OTA v2 feature-plan generation."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "ci/plan-feature-transaction.py"
PLATFORM = SCRIPT.parents[3] / "platform/tools/mt8163-arm32"
FEATURES = ("airplay2", "tts", "wakeword", "stt", "assistant")
DAEMONS = {
    "airplay2": "usr/local/sbin/libreecho-audio-engine",
    "tts": "usr/local/sbin/libreecho-ttsd",
    "wakeword": "usr/local/sbin/libreecho-waked",
    "stt": "usr/local/sbin/libreecho-sttd",
    "assistant": "usr/local/sbin/libreecho-agentd",
}
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_catalog(root: Path, name: str, changed: bool = False) -> Path:
    tree = root / name
    tree.mkdir()
    records: dict[str, object] = {}
    for feature in FEATURES:
        payload = tree / f"{feature}.squashfs"
        content = (feature + ("-new" if changed and feature == "assistant" else "-base")).encode()
        payload.write_bytes(content)
        daemon_hash = digest(payload)
        manifest = tree / f"{feature}.manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "feature_id": feature,
            "format": "squashfs-lz4",
            "payload": {"filename": payload.name, "sha256": daemon_hash, "size": len(content)},
            "files": {DAEMONS[feature]: {"sha256": daemon_hash, "size": len(content), "mode": "0755"}},
        }, indent=2, sort_keys=True) + "\n")
        records[feature] = {
            "payload": {"path": str(payload), "sha256": digest(payload), "size": payload.stat().st_size},
            "manifest": {"path": str(manifest), "sha256": digest(manifest), "size": manifest.stat().st_size},
        }
    path = root / f"{name}.json"
    path.write_text(json.dumps({"features": records}, indent=2) + "\n")
    return path


def run_plan(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *args], text=True, capture_output=True)


def real_catalog(root: Path, name: str, assistant_content: bytes) -> tuple[Path, Path]:
    """Produce real lz4 SquashFS producer inputs for planner consumer tests."""
    catalog_root = root / f"{name}-payloads"
    catalog_root.mkdir()
    records: dict[str, object] = {}
    for feature in FEATURES:
        feature_root = catalog_root / feature
        target = feature_root / DAEMONS[feature]
        target.parent.mkdir(parents=True)
        target.write_bytes(assistant_content if feature == "assistant" else feature.encode())
        target.chmod(0o755)
        payload = catalog_root / f"{feature}.squashfs"
        made = subprocess.run([
            "mksquashfs", str(feature_root), str(payload), "-noappend", "-comp", "lz4",
            "-all-root", "-no-xattrs", "-mkfs-time", "0", "-all-time", "0",
            "-root-mode", "0755", "-no-progress",
        ], text=True, capture_output=True)
        if made.returncode != 0:
            raise RuntimeError(made.stderr)
        manifest = catalog_root / f"{feature}.manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1, "feature_id": feature, "format": "squashfs-lz4",
            "payload": {"filename": payload.name, "sha256": digest(payload), "size": payload.stat().st_size},
            "files": {DAEMONS[feature]: {"sha256": digest(target), "size": target.stat().st_size, "mode": "0755"}},
        }, indent=2, sort_keys=True) + "\n")
        records[feature] = {
            "payload": {"path": str(payload), "sha256": digest(payload), "size": payload.stat().st_size},
            "manifest": {"path": str(manifest), "sha256": digest(manifest), "size": manifest.stat().st_size},
        }
    catalog = root / f"{name}.json"
    catalog.write_text(json.dumps({"features": records}, indent=2) + "\n")
    return catalog, catalog_root


class FeaturePlanTests(unittest.TestCase):
    def common_args(self, base: Path, candidate: Path, output: Path, inventory: Path) -> list[str]:
        return [
            "--base-catalog", str(base),
            "--candidate-catalog", str(candidate),
            "--release", "0.13.11",
            "--source-commit", COMMIT,
            "--output", str(output),
            "--inventory-output", str(inventory),
        ]

    def test_output_is_the_platform_v2_record_list_and_inventory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="feature-plan-") as directory:
            root = Path(directory)
            base = write_catalog(root, "base")
            candidate = write_catalog(root, "candidate", changed=True)
            output, inventory = root / "plan.json", root / "inventory.json"
            result = run_plan(*self.common_args(base, candidate, output, inventory))
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(output.read_text())
            self.assertEqual([record["feature_id"] for record in plan["features"]], list(FEATURES))
            self.assertEqual(plan["features"][4]["action"], "replace")
            self.assertEqual(plan["features"][4]["asset"], "libreecho-radar-puffin-0.13.11-assistant.payload.squashfs")
            self.assertEqual(plan["features"][1]["action"], "preserve")
            self.assertNotIn("path", plan["features"][4])
            inventory_data = json.loads(inventory.read_text())
            self.assertEqual(inventory_data["transaction_type"], "system")
            self.assertEqual([asset["name"] for asset in inventory_data["assets"]], [
                "libreecho-radar-puffin-0.13.11-assistant.manifest.json",
                "libreecho-radar-puffin-0.13.11-assistant.payload.squashfs",
            ])

    def test_runtime_is_selected_only_for_a_compatible_capsule_pair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="feature-plan-runtime-") as directory:
            root = Path(directory)
            base = write_catalog(root, "base")
            candidate = write_catalog(root, "candidate", changed=True)
            base_data = json.loads(base.read_text())
            assistant = base_data["features"]["assistant"]
            runtime = root / "runtime"
            runtime.mkdir()
            payload = runtime / "assistant.runtime.squashfs"
            payload.write_bytes(b"assistant-new")
            runtime_manifest = runtime / "assistant.runtime-manifest.json"
            runtime_manifest.write_text(json.dumps({
                "schema_version": 1,
                "kind": "runtime-capsule",
                "feature_id": "assistant",
                "format": "squashfs-lz4",
                "base_payload_sha256": assistant["payload"]["sha256"],
                "base_manifest_sha256": assistant["manifest"]["sha256"],
                "payload": {"filename": payload.name, "sha256": digest(payload), "size": payload.stat().st_size},
                "files": {DAEMONS["assistant"]: {"sha256": digest(payload), "size": 7, "mode": "0755"}},
            }, indent=2) + "\n")
            output, inventory = root / "plan.json", root / "inventory.json"
            result = run_plan(*self.common_args(base, candidate, output, inventory), "--runtime-dir", str(runtime), "--asset-output-dir", str(root / "assets"))
            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads(output.read_text())["features"][4]
            self.assertEqual(record["action"], "runtime")
            self.assertEqual(record["asset"], "libreecho-radar-puffin-0.13.11-assistant.runtime.squashfs")
            self.assertTrue((root / "assets" / record["asset"]).is_file())

    def test_real_platform_capsule_producer_and_product_consumer_verify_semantics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="feature-plan-real-runtime-") as directory:
            root = Path(directory)
            base, base_root = real_catalog(root, "base", b"assistant-base")
            candidate, _ = real_catalog(root, "candidate", b"assistant-new")
            runtime = root / "runtime"
            runtime.mkdir()
            source = root / "replacement"
            source.write_bytes(b"assistant-new")
            built = subprocess.run([
                sys.executable, str(PLATFORM / "feature_runtime/package_runtime.py"),
                "--feature-id", "assistant", "--base-payload", str(base_root / "assistant.squashfs"),
                "--base-manifest", str(base_root / "assistant.manifest.json"),
                "--product-release", "0.13.11", "--source-commit", COMMIT,
                "--component", "libreecho-agentd", "--component-version", "0.13.11",
                "--build-identity", "fixture-build", "--service-dependency", "libreecho-runtime-base",
                "--compatibility", json.dumps({"abi": "arm32-linux-gnueabihf-v1", "model": "mt8163-radar-puffin", "mounts": ["/usr/local/sbin"], "dependencies": ["libreecho-runtime-base"]}, sort_keys=True),
                "--replacement", f"usr/local/sbin/libreecho-agentd={source}", "--max-bytes", "1048576",
                "--output", str(runtime / "assistant.runtime.squashfs"),
                "--manifest", str(runtime / "assistant.runtime-manifest.json"),
            ], text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = run_plan(*self.common_args(base, candidate, root / "plan.json", root / "inventory.json"),
                              "--runtime-dir", str(runtime), "--asset-output-dir", str(root / "assets"),
                              "--platform-runtime-verifier", str(PLATFORM / "feature_runtime/verify_runtime.py"))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / "plan.json").read_text())["features"][4]["action"], "runtime")

    def test_real_verifier_rejects_internally_consistent_non_squashfs_capsule(self) -> None:
        with tempfile.TemporaryDirectory(prefix="feature-plan-malformed-runtime-") as directory:
            root = Path(directory)
            base, base_root = real_catalog(root, "base", b"assistant-base")
            candidate, _ = real_catalog(root, "candidate", b"assistant-new")
            runtime = root / "runtime"
            runtime.mkdir()
            source = root / "replacement"
            source.write_bytes(b"assistant-new")
            output = runtime / "assistant.runtime.squashfs"
            manifest_path = runtime / "assistant.runtime-manifest.json"
            built = subprocess.run([
                sys.executable, str(PLATFORM / "feature_runtime/package_runtime.py"),
                "--feature-id", "assistant", "--base-payload", str(base_root / "assistant.squashfs"),
                "--base-manifest", str(base_root / "assistant.manifest.json"),
                "--product-release", "0.13.11", "--source-commit", COMMIT,
                "--component", "libreecho-agentd", "--component-version", "0.13.11",
                "--build-identity", "fixture-build", "--service-dependency", "libreecho-runtime-base",
                "--compatibility", json.dumps({"abi": "arm32-linux-gnueabihf-v1", "model": "mt8163-radar-puffin", "mounts": ["/usr/local/sbin"], "dependencies": ["libreecho-runtime-base"]}, sort_keys=True),
                "--replacement", f"usr/local/sbin/libreecho-agentd={source}", "--max-bytes", "1048576",
                "--output", str(output), "--manifest", str(manifest_path),
            ], text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            output.write_bytes(b"not-a-squashfs")
            capsule_manifest = json.loads(manifest_path.read_text())
            capsule_manifest["payload"] = {"filename": output.name, "sha256": digest(output), "size": output.stat().st_size}
            manifest_path.write_text(json.dumps(capsule_manifest, indent=2, sort_keys=True) + "\n")
            result = run_plan(*self.common_args(base, candidate, root / "plan.json", root / "inventory.json"),
                              "--runtime-dir", str(runtime),
                              "--platform-runtime-verifier", str(PLATFORM / "feature_runtime/verify_runtime.py"))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Platform runtime verifier", result.stderr)

    def test_incompatible_runtime_capsule_is_an_explicit_blocker(self) -> None:
        with tempfile.TemporaryDirectory(prefix="feature-plan-incompatible-") as directory:
            root = Path(directory)
            base = write_catalog(root, "base")
            candidate = write_catalog(root, "candidate", changed=True)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "assistant.runtime.squashfs").write_bytes(b"capsule")
            (runtime / "assistant.runtime-manifest.json").write_text(json.dumps({
                "schema_version": 1, "kind": "runtime-capsule", "feature_id": "assistant",
                "format": "squashfs-lz4", "base_payload_sha256": "f" * 64,
                "base_manifest_sha256": "e" * 64,
                "payload": {"filename": "assistant.runtime.squashfs", "sha256": "a" * 64, "size": 7},
                "files": {DAEMONS["assistant"]: {"sha256": "b" * 64, "size": 7, "mode": "0755"}},
            }))
            result = run_plan(*self.common_args(base, candidate, root / "plan.json", root / "inventory.json"), "--runtime-dir", str(runtime))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("runtime capsule", result.stderr)

    def test_build_script_wires_v2_plan_to_builder_and_keeps_v1_default(self) -> None:
        build = (SCRIPT.parents[1] / "build.sh").read_text()
        self.assertIn('OTA_FORMAT="${LIBREECHO_OTA_FORMAT:-v1}"', build)
        self.assertIn('--feature-plan "$feature_plan"', build)
        self.assertIn('--platform-runtime-verifier "$PLATFORM_RUNTIME_VERIFIER"', build)
        self.assertIn('--format "$OTA_FORMAT"', build)
        self.assertIn('--platform-source "$TOOLING_SRC"', build)
        self.assertIn('--platform-tool "$OTA_DIR/make_ota_bundle.py"', build)
        self.assertIn("LIBREECHO_OTA_BASE_CATALOG", build)

    def test_v2_stages_run_local_public_key_before_handoff(self) -> None:
        build = (SCRIPT.parents[1] / "build.sh").read_text()
        key_stage = 'install -m 0644 "$OTA_PUBLIC_KEY" "$RUN/ota-public-key.hex"'
        handoff = 'python3 -B "$PIPELINE/ci/sign_ota_candidate.py" create-handoff'
        self.assertIn(key_stage, build)
        self.assertLess(build.index(key_stage), build.index(handoff))


if __name__ == "__main__":
    unittest.main()
