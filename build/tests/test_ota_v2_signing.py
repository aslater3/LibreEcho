#!/usr/bin/env python3
"""Host contracts for Product OTA v2 inputs and protected signing handoff."""
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
from unittest import mock

CI = Path(__file__).resolve().parents[1] / "ci"
ROOT = CI.parents[1]
sys.path.insert(0, str(CI))
from ota_v2_inputs import InputError, validate_inputs, verify_catalog  # noqa: E402
from sign_ota_candidate import (  # noqa: E402
    HandoffError,
    _check_platform_binding,
    _check_product_runtime_binding,
    _dependency_tree_hash,
    _platform_identity,
    _product_runtime_identity,
    create_handoff,
    sign,
    invoke_platform_signer,
    validate_handoff,
)
from ota_v2_product import (  # noqa: E402
    ContractError,
    validate_control_tar,
    validate_v2_publisher_asset_set,
)

PLATFORM_ROOT = Path(os.environ.get(
    "LIBREECHO_PLATFORM_SRC",
    str(ROOT.parent / "platform"),
))
PLATFORM_TOOL = PLATFORM_ROOT / "tools/mt8163-arm32/ota/make_ota_bundle.py"


class OtaV2InputTests(unittest.TestCase):
    def test_v1_is_the_safe_default_bridge(self) -> None:
        self.assertEqual(validate_inputs("v1", "", "", "", "push", "refs/heads/main"), {
            "ota_format": "v1",
            "ota_release": "",
            "ota_base_catalog_url": "",
            "ota_base_catalog_sha256": "",
        })

    def test_v2_requires_the_actual_01313_candidate_and_pinned_prior_catalog(self) -> None:
        with self.assertRaisesRegex(InputError, "0.13.13"):
            validate_inputs(
                "v2", "0.14.0",
                "https://github.com/aslater3/LibreEcho/releases/download/radar-puffin-v0.13.12/libreecho-radar-puffin-v0.13.12-feature-catalog.json",
                "a" * 64, "workflow_dispatch", "refs/heads/release/0.13.13",
            )
        with self.assertRaises(InputError):
            validate_inputs(
                "v2", "0.13.13",
                "https://attacker.invalid/$(touch-pwned).json", "a" * 64,
                "workflow_dispatch", "refs/heads/release/0.13.13",
            )
        accepted = validate_inputs(
            "v2", "0.13.13",
            "https://github.com/aslater3/LibreEcho/releases/download/radar-puffin-v0.13.12/libreecho-radar-puffin-v0.13.12-feature-catalog.json",
            "a" * 64, "workflow_dispatch", "refs/heads/release/0.13.13",
        )
        self.assertEqual(accepted["ota_format"], "v2")

    def test_base_catalog_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(InputError, "hash mismatch"):
                verify_catalog(path, "a" * 64)

    def test_v2_publisher_gate_rejects_an_extra_capsule(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-v2-publisher-gate-") as directory:
            output = Path(directory)
            expected = [
                {"name": "libreecho-radar-puffin-0.13.13-assistant.runtime.squashfs"},
                {"name": "libreecho-radar-puffin-0.13.13-assistant.runtime-manifest.json"},
            ]
            for item in expected:
                (output / item["name"]).write_bytes(b"fixture")
            (output / "libreecho-radar-puffin-0.13.13-extra.payload.squashfs").write_bytes(b"extra")
            with self.assertRaisesRegex(ContractError, "publisher asset set"):
                validate_v2_publisher_asset_set(output, "0.13.13", expected)


class OtaV2SigningHandoffTests(unittest.TestCase):
    def test_dependency_tree_hash_includes_native_extension_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-dependency-tree-") as directory:
            root = Path(directory) / "nacl"
            root.mkdir()
            native = root / "_sodium.abi3.so"
            python_file = root / "bindings.py"
            native.write_bytes(b"native-before")
            python_file.write_text("bindings = True\n", encoding="utf-8")
            before = _dependency_tree_hash(root)
            native.write_bytes(b"native-after")
            self.assertNotEqual(before, _dependency_tree_hash(root))

    def test_product_runtime_binding_rejects_orchestrator_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-product-runtime-") as directory:
            root = Path(directory)
            interpreter = root / "python"
            signer = root / "signer.py"
            parser = root / "parser.py"
            orchestrator = root / "build.sh"
            for path in (interpreter, signer, parser, orchestrator):
                path.write_bytes(path.name.encode("ascii"))
            identity = {
                "interpreter": {"name": interpreter.name, "path": str(interpreter),
                                "size": interpreter.stat().st_size,
                                "sha256": hashlib.sha256(interpreter.read_bytes()).hexdigest()},
                "product_tools": [
                    {"name": path.name, "path": str(path), "size": path.stat().st_size,
                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "mode": path.stat().st_mode & 0o7777}
                    for path in (signer, parser, orchestrator)
                ],
                "packages": [],
                "excluded": ["**/__pycache__/**"],
            }
            orchestrator.write_bytes(b"mutated")
            with self.assertRaisesRegex(HandoffError, "Product signing runtime identity changed"):
                _check_product_runtime_binding(identity)

        with tempfile.TemporaryDirectory() as directory:
            handoff = Path(directory) / "ota-signing-handoff.json"
            handoff.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
            with self.assertRaisesRegex(HandoffError, "schema mismatch"):
                validate_handoff(handoff)

    def test_handoff_schema_and_asset_hashes_are_rechecked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset = root / "payload.squashfs"
            asset.write_bytes(b"candidate")
            handoff = root / "ota-signing-handoff.json"
            def record(name: str) -> dict[str, object]:
                return {"name": name, "path": str(asset), "size": asset.stat().st_size, "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}
            data = {
                "schema": "libreecho-ota-v2-signing-handoff-v1", "format": "v2",
                "release": "0.13.13", "source_commit": "a" * 40,
                "update_channel": "stable", "base_catalog_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                "run_dir": str(root), "base_catalog": record(asset.name),
                "build_manifest": record(asset.name),
                "boot_image": record(asset.name), "feature_plan": record(asset.name),
                "feature_asset_inventory": record(asset.name),
                "assets": [{**record(asset.name), "sha256": "b" * 64}],
            }
            handoff.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(HandoffError, "schema mismatch"):
                validate_handoff(handoff, "0.13.13")

    def test_actual_platform_v1_bundle_is_verified_by_product_with_ephemeral_key(self) -> None:
        from nacl.signing import SigningKey
        with tempfile.TemporaryDirectory(prefix="ota-v1-control-") as directory:
            root = Path(directory)
            boot = root / "boot.img"
            boot.write_bytes(b"ANDROID!" + bytes(16 * 1024 * 1024 - 8))
            signing = SigningKey.generate()
            signing_path = root / "signing.hex"
            public_path = root / "public.hex"
            signing_path.write_text(signing.encode().hex() + "\n")
            public_path.write_text(signing.verify_key.encode().hex() + "\n")
            build = root / "build.json"
            build.write_text(json.dumps({
                "output": {"sha256": hashlib.sha256(boot.read_bytes()).hexdigest(), "size": 16 * 1024 * 1024},
                "image_profile": "ota", "service_profile": "production",
                "feature_policy": "community-noncommercial", "update_channel": "dev",
            }))
            output = root / "bundle.ota.tar"
            result = subprocess.run([
                sys.executable, str(PLATFORM_TOOL), "--format", "v1",
                "--boot-image", str(boot), "--build-manifest", str(build),
                "--version", "fixture-v1", "--signing-key", str(signing_path),
                "--public-key", str(public_path), "--service-profile", "production",
                "--feature-policy", "community-noncommercial", "--update-channel", "dev",
                "--output", str(output),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            validate_control_tar(output, public_path, "v1", "")
            raw_bundle = bytearray(output.read_bytes())
            with tarfile.open(output, "r") as archive:
                signature = archive.extractfile("manifest.sig").read()
            signature_offset = raw_bundle.find(signature)
            self.assertGreaterEqual(signature_offset, 0)
            raw_bundle[signature_offset] = ord("0") if raw_bundle[signature_offset] != ord("0") else ord("1")
            output.write_bytes(raw_bundle)
            with self.assertRaisesRegex(ContractError, "signature|malformed"):
                validate_control_tar(output, public_path, "v1", "")

    def test_platform_tool_substitution_is_rejected_before_signer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ota-platform-binding-") as directory:
            identity = _platform_identity(PLATFORM_ROOT, PLATFORM_TOOL)
            binding = {
                "platform_source": {
                    "path": identity["path"], "commit": identity["commit"],
                    "diff_sha256": identity["diff_sha256"],
                },
                "platform_tool": identity["tool"],
                "platform_dependencies": identity["dependencies"],
            }
            substitute = Path(directory) / PLATFORM_TOOL.name
            substitute.write_bytes(PLATFORM_TOOL.read_bytes() + b"\n# substituted\n")
            with self.assertRaisesRegex(HandoffError, "changed or was substituted"):
                _check_platform_binding(binding, substitute)

    def test_actual_v2_platform_signer_consumes_handoff_and_product_rechecks_bundle(self) -> None:
        from build.tests.test_prepare_dev_release import add_v2_contract, fixture
        from nacl.signing import SigningKey
        with tempfile.TemporaryDirectory(prefix="ota-v2-signer-fixture-") as directory:
            root = Path(directory)
            run, commits = fixture(root)
            add_v2_contract(run, commits, release="0.13.13")
            build_manifest = json.loads((run / "manifest.json").read_text())
            build_manifest["update_channel"] = "stable"
            (run / "manifest.json").write_text(json.dumps(build_manifest))
            base = run / "base-catalog.json"
            base.write_text("{}\n")
            signing = SigningKey.generate()
            signing_path = root / "signing.hex"
            public_path = root / "public.hex"
            signing_path.write_text(signing.encode().hex() + "\n")
            public_path.write_text(signing.verify_key.encode().hex() + "\n")
            (run / "ota-public-key.hex").write_bytes(public_path.read_bytes())
            handoff = run / "ota-signing-handoff.json"
            anchor = hashlib.sha256(public_path.read_bytes()).hexdigest()
            with mock.patch.dict(os.environ, {"LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256": anchor}):
                create_handoff(
                    run, "0.13.13", commits["ui"], "stable", base,
                    hashlib.sha256(base.read_bytes()).hexdigest(), handoff,
                    PLATFORM_ROOT, PLATFORM_TOOL,
                )
                output = root / "signed-v2.ota.tar"
                sign(handoff, PLATFORM_TOOL, signing_path, public_path, output)
            self.assertTrue(output.is_file())
            with tarfile.open(output) as archive:
                self.assertEqual(archive.getnames(), ["manifest", "manifest.sig", "boot.img"])
            substitute = root / "substituted-tool.py"
            substitute.write_bytes(PLATFORM_TOOL.read_bytes() + b"\n# substitution\n")
            blocked = root / "blocked.ota.tar"
            with mock.patch.dict(os.environ, {"LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256": anchor}), self.assertRaisesRegex(HandoffError, "changed or was substituted"):
                sign(handoff, substitute, signing_path, public_path, blocked)
            self.assertFalse(blocked.exists())

    def test_signer_invocation_is_argument_vector_and_binds_v2_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / "feature-plan.json"
            plan.write_text("{}\n", encoding="utf-8")
            boot = root / "boot.img"
            manifest = root / "manifest.json"
            key = root / "key.hex"
            public = root / "public.hex"
            output = root / "ota.tar"
            with mock.patch("sign_ota_candidate.subprocess.run") as run:
                command = invoke_platform_signer(
                    platform_tool=root / "make_ota_bundle.py", boot_image=boot,
                    build_manifest=manifest, signing_key=key, public_key=public,
                    output=output, plan=plan, release="0.13.13",
                    update_channel="stable",
                )
                self.assertIn("--format", command)
                self.assertIn("v2", command)
                self.assertIn("--feature-plan", command)
                self.assertIn(str(plan), command)
                self.assertEqual(command[0], sys.executable)
                run.assert_called_once_with(command, check=True)
                self.assertIs(run.call_args.kwargs.get("shell", False), False)


if __name__ == "__main__":
    unittest.main()
