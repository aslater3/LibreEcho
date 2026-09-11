#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import shutil
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_test_provenance(path: Path, release_scope: str) -> None:
    path.write_text(json.dumps({
        "release_id": "radar-puffin-v0.1.0",
        "release_scope": release_scope,
        "sources": {
            name: {"repository": f"https://github.com/example/{name}", "commit": digit * 40}
            for name, digit in {
                "product": "1", "kernel": "2", "tooling": "3", "ui": "4"
            }.items()
        },
    }))


PREPARE_RELEASE = load_module("prepare_release", ROOT / "tools/prepare-release.py")
PUBLIC_METADATA = load_module(
    "check_public_metadata", ROOT / "tools/check-public-metadata.py"
)
INSTALLER = load_module("libreecho_install", ROOT / "tools/libreecho-install.py")


def fake_executable(root: Path, name: str) -> Path:
    path = root / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    path.chmod(0o755)
    return path


class OneShotFastbootTests(unittest.TestCase):
    def test_prepare_fastboot_tools_stages_complete_toolset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fastboot = fake_executable(root, "fastboot")
            fake_executable(root, "mke2fs")
            img2simg = fake_executable(root, "img2simg")
            with mock.patch.object(INSTALLER, "_find_img2simg", return_value=img2simg):
                staged = INSTALLER.prepare_fastboot_tools(str(fastboot), root / "cache")
            staged_path = Path(staged)
            self.assertEqual(staged_path.parent, root / "cache" / "host-tools")
            for name in ("fastboot", "mke2fs", "img2simg"):
                self.assertTrue((staged_path.parent / name).is_file())
            result = subprocess.run([staged, "--version"], text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0)

    def test_prepare_fastboot_tools_fails_before_device_when_helper_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fastboot = fake_executable(root, "fastboot")
            with mock.patch.object(INSTALLER, "_find_mke2fs", return_value=None), \
                 mock.patch.object(INSTALLER, "_find_img2simg", return_value=None), \
                 mock.patch.object(INSTALLER, "_install_host_format_tools") as installer:
                with self.assertRaisesRegex(INSTALLER.InstallerError, "--install-host-deps"):
                    INSTALLER.prepare_fastboot_tools(str(fastboot), root / "cache")
            installer.assert_not_called()

    def test_prepare_fastboot_tools_can_request_missing_dependency_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fastboot = fake_executable(root, "fastboot")
            mke2fs = fake_executable(root, "mke2fs")
            img2simg = fake_executable(root, "img2simg")
            with mock.patch.object(INSTALLER, "_find_mke2fs", side_effect=[None, mke2fs]), \
                 mock.patch.object(INSTALLER, "_find_img2simg", side_effect=[None, img2simg]), \
                 mock.patch.object(INSTALLER, "_install_host_format_tools") as installer:
                staged = INSTALLER.prepare_fastboot_tools(
                    str(fastboot), root / "cache", install_host_deps=True
                )
        installer.assert_called_once_with()
        self.assertTrue(staged.endswith("/host-tools/fastboot"))

    def test_state_accepts_legacy_without_userdata_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            path.write_text(json.dumps({
                "phase": "READBACK_VERIFIED",
                "release": "radar-puffin-v0.13.7",
                "bundle_sha256": "a" * 64,
            }))
            state = INSTALLER._read_state(path)
        self.assertFalse(state.get("userdata_formatted", False))

    def test_state_accepts_userdata_format_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            path.write_text(json.dumps({
                "phase": "READBACK_VERIFIED",
                "release": "radar-puffin-v0.13.7",
                "bundle_sha256": "a" * 64,
                "userdata_formatted": True,
            }))
            state = INSTALLER._read_state(path)
        self.assertTrue(state["userdata_formatted"])

    def test_fastboot_partition_size_parses_hex_prefix(self) -> None:
        result = subprocess.CompletedProcess(
            ["fastboot"], 0, "", "partition-size:userdata: 0x41380000\n"
        )
        with mock.patch.object(INSTALLER, "_run_command", return_value=result):
            self.assertEqual(
                INSTALLER._fastboot_partition_size("fastboot", "SERIAL", "userdata"),
                INSTALLER.USERDATA_BYTES,
            )

    def test_userdata_format_rejects_unexpected_partition_size(self) -> None:
        with mock.patch.object(INSTALLER, "verify_fastboot_product"), \
             mock.patch.object(INSTALLER, "_fastboot_partition_size", return_value=INSTALLER.USERDATA_BYTES + 512), \
             mock.patch.object(INSTALLER, "_run_command") as command:
            with self.assertRaisesRegex(INSTALLER.InstallerError, "userdata partition size mismatch"):
                INSTALLER.format_userdata_in_fastboot("fastboot", "SERIAL", 120)
        command.assert_not_called()

    def test_userdata_format_avoids_fastboot_internal_formatter(self) -> None:
        source = (ROOT / "tools/libreecho-install.py").read_text(encoding="utf-8")
        self.assertIn('"flash", "userdata", str(sparse)', source)
        self.assertIn("^64bit,^metadata_csum,^metadata_csum_seed,^orphan_file", source)

    def test_adb_diagnostics_capture_read_only_command_bundle(self) -> None:
        calls = []
        result = subprocess.CompletedProcess(["adb"], 0, "diagnostic output\n", "")

        def fake_run(argv, timeout, *, check=True):
            calls.append((argv, timeout, check))
            return result

        with mock.patch.object(INSTALLER, "_run_command", side_effect=fake_run), \
             mock.patch.object(INSTALLER, "_append_log") as log:
            INSTALLER.collect_adb_diagnostics("adb", "SERIAL", 30, "test")
        remote = [call[0][4:] for call in calls]
        self.assertIn(["id"], remote)
        self.assertIn(["cat", "/proc/mounts"], remote)
        self.assertIn(["blkid", "/dev/mmcblk0p16"], remote)
        self.assertIn(["dmesg"], remote)
        self.assertTrue(any("ADB_DIAGNOSTICS begin" in str(call.args[0]) for call in log.call_args_list))
        self.assertTrue(any("ADB_DIAGNOSTICS end" in str(call.args[0]) for call in log.call_args_list))

    def test_wait_for_transport_does_not_artificially_cap_slow_probe(self) -> None:
        result = subprocess.CompletedProcess(["probe"], 0, "device\n", "")
        with mock.patch.object(INSTALLER.subprocess, "run", return_value=result) as run:
            INSTALLER.wait_for_transport(["probe"], "device", 60, "ADB")
        self.assertGreater(run.call_args.kwargs["timeout"], 10)

    def test_source_announces_fastboot_and_payload_boundaries(self) -> None:
        source = (ROOT / "tools/libreecho-install.py").read_text(encoding="utf-8")
        for marker in (
            "FASTBOOT STAGE: waiting for the unlocked fastboot device.",
            "FASTBOOT STAGE: detected device",
            "FASTBOOT STAGE: userdata filesystem format complete.",
            "FASTBOOT STAGE: flashing verified boot payload to boot_",
            "PAYLOAD STAGE: beginning verified feature payload staging.",
        ):
            self.assertIn(marker, source)

    def test_feature_stager_syncs_committed_files_before_removing_marker(self) -> None:
        stager = INSTALLER.ROOT_FEATURE_STAGER
        move = stager.index('$BB mv "$DEST/staging/payload.squashfs.new" "$DEST/payload.squashfs"')
        manifest = stager.index('$BB mv "$DEST/staging/manifest.json.new" "$DEST/manifest.json"', move)
        first_sync = stager.index("$BB sync", manifest)
        cleanup = stager.index('$BB rmdir "$DEST/staging"', manifest)
        second_sync = stager.index("$BB sync", cleanup)
        self.assertLess(move, manifest)
        self.assertLess(manifest, first_sync)
        self.assertLess(first_sync, cleanup)
        self.assertLess(cleanup, second_sync)
        self.assertIn(
            "|| { echo FEATURE_STAGE_COMMIT_SYNC_FAILED; exit 1; }",
            stager[first_sync:first_sync + 100],
        )
        self.assertIn(
            "|| { echo FEATURE_STAGE_STAGING_CLEANUP_FAILED; exit 1; }",
            stager[cleanup:cleanup + 100],
        )
        self.assertIn(
            "|| { echo FEATURE_STAGE_MARKER_SYNC_FAILED; exit 1; }",
            stager[second_sync:second_sync + 100],
        )


class PublicMetadataTests(unittest.TestCase):
    def test_versioned_source_url_is_not_an_ipv4_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.md").write_text(
                "https://launchpad.net/source/2025.10.07-0ubuntu1~24.04.1/archive.tar.xz"
            )
            self.assertEqual(PUBLIC_METADATA.violations(root), [])

    def test_wildcard_serial_device_documentation_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "installer.py").write_text(
                "scan /dev/ttyACM* and /dev/ttyUSB*; do not use a concrete node\n",
                encoding="utf-8",
            )
            self.assertEqual(PUBLIC_METADATA.violations(root), [])

    def test_concrete_serial_device_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "metadata.txt").write_text(
                "captured from /dev/ttyACM0 and /dev/ttyUSB1\n",
                encoding="utf-8",
            )
            failures = PUBLIC_METADATA.violations(root)
            self.assertEqual(len(failures), 2)
            self.assertTrue(all("concrete serial device path" in item for item in failures))

    def test_private_identifiers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "private.md").write_text(
                "/home/operator/build 192.168.10.42 AA:BB:CC:DD:EE:FF"
            )
            failures = PUBLIC_METADATA.violations(root)
            self.assertEqual(len(failures), 3)
            self.assertTrue(any("private marker" in item for item in failures))
            self.assertTrue(any("private IPv4" in item for item in failures))
            self.assertTrue(any("MAC address" in item for item in failures))

    def test_run_ids_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for run_id in (
                "20260811T214603Z-a6c4b01faae9-clean-ota",
                "20260811T214603Z_clean-ota",
                "20260811T214603Z-",
                "20260811T214603Z_",
                "20260811T214603Z",
            ):
                (root / f"{run_id}.json").write_text("sanitized")
            nested = root / "20260811T214603Z" / "metadata.json"
            nested.parent.mkdir()
            nested.write_text("sanitized")
            failures = PUBLIC_METADATA.violations(root)
            for run_id in (
                "20260811T214603Z-a6c4b01faae9-clean-ota",
                "20260811T214603Z_clean-ota",
                "20260811T214603Z-",
                "20260811T214603Z_",
                "20260811T214603Z",
            ):
                with self.subTest(run_id=run_id):
                    self.assertTrue(any(f"{run_id}.json" in item for item in failures))
            self.assertTrue(any("20260811T214603Z/metadata.json" in item for item in failures))

    def test_prepare_release_allows_wildcard_serial_documentation(self) -> None:
        data = json.loads((ROOT / "release/components.json").read_text())
        audio = dict(next(c for c in data["components"] if c["id"] == "mt8163-audio-fpga"))
        audio["download_location"] = "documented device class /dev/ttyACM*"
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "release").mkdir()
            (repository / "release/THIRD_PARTY_NOTICES.md").write_text("notices\n")
            (repository / "release/FPGA-PROVENANCE.md").write_text("documented\n")
            catalog = repository / "release/components.json"
            catalog.write_text(json.dumps({"schema_version": 2, "components": [audio]}))
            self.assertEqual(PREPARE_RELEASE.load_components(catalog), [audio])

    def test_prepare_release_rejects_concrete_serial_device_path(self) -> None:
        data = json.loads((ROOT / "release/components.json").read_text())
        audio = dict(next(c for c in data["components"] if c["id"] == "mt8163-audio-fpga"))
        audio["download_location"] = "captured from /dev/ttyACM0"
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "release").mkdir()
            (repository / "release/THIRD_PARTY_NOTICES.md").write_text("notices\n")
            (repository / "release/FPGA-PROVENANCE.md").write_text("documented\n")
            catalog = repository / "release/components.json"
            catalog.write_text(json.dumps({"schema_version": 2, "components": [audio]}))
            with self.assertRaises(SystemExit) as failure:
                PREPARE_RELEASE.load_components(catalog)
        self.assertIn("private value", str(failure.exception))


class ComponentGateTests(unittest.TestCase):
    def test_public_catalog_scopes_noncommercial_wakeword(self) -> None:
        unrestricted = PREPARE_RELEASE.load_components(
            ROOT / "release/components.json", "commercially-unrestricted"
        )
        community = PREPARE_RELEASE.load_components(
            ROOT / "release/components.json", "community-noncommercial"
        )
        data = json.loads((ROOT / "release/components.json").read_text())
        self.assertEqual(len(data["components"]), 18)
        self.assertNotIn("wakeword-payload", {c["id"] for c in unrestricted})
        self.assertIn("wakeword-payload", {c["id"] for c in community})
        for component_id in (
            "core-runtime-closure", "airplay-payload", "stt-payload",
            "tts-payload", "assistant-payload",
        ):
            component = next(c for c in data["components"] if c["id"] == component_id)
            self.assertEqual(component["release_status"], "cleared")
        wakeword = next(c for c in data["components"] if c["id"] == "wakeword-payload")
        self.assertEqual(wakeword["distribution_scope"], "separate-payload")
        self.assertEqual(wakeword["release_status"], "cleared")
        self.assertEqual(
            wakeword["allowed_release_scopes"], ["community-noncommercial"]
        )
        self.assertEqual(wakeword["use_restriction"], "noncommercial-model-asset")
        audio = next(c for c in data["components"] if c["id"] == "mt8163-audio-fpga")
        self.assertEqual(audio["license"], "NOASSERTION")
        self.assertEqual(audio["release_status"], "documented-good-faith")
        self.assertTrue(audio["included_in_candidate"])
        self.assertEqual(audio["known_good_size"], 30964)
        self.assertEqual(audio["known_good_sha256"], "77a558bacdaaf9e343f02f2d74f27a5f2bb2dc8b6d66cc2499b60ed14ef62fe6")
        self.assertIn("audio-capable candidate", (ROOT / "release/THIRD_PARTY_NOTICES.md").read_text())
        self.assertNotIn("therefore excludes it from public artifacts", (ROOT / "release/THIRD_PARTY_NOTICES.md").read_text())

    def test_community_source_offer_hashes_match_catalog_and_closure(self) -> None:
        expected = {
            "core-runtime-closure": "3e4f611fa07044c1e8e0060b7a1d9cc356493dfb42b963a82eccb9e9ff125952",
            "airplay-payload": "f159ecdb4e0381433c78c4e80a360bc6c3eb45e4c0c7f4caadbbe355c37a6031",
            "stt-payload": "e5ccaaed9380493bde952f5435ef6612d60b116c6c6e18bb6f00110d95742d03",
            "tts-payload": "22be3e3cfc0446991a0a9c85c08c39d77212d44684322f6af1d9fa30761e9447",
            "wakeword-payload": "8be7517a3f2feff5effe36f259ec2c35e3ffeded779fbfc4386f0c5bcb9833ac",
            "assistant-payload": "85ee50f6befa873345b7444510c988e3625987fb4032170099d7c64f27541027",
        }
        data = json.loads((ROOT / "release/components.json").read_text())
        closure = (
            ROOT / "release/COMMUNITY-NONCOMMERCIAL-SOURCE-CLOSURE.md"
        ).read_text()
        by_id = {component["id"]: component for component in data["components"]}
        for component_id, digest in expected.items():
            with self.subTest(component=component_id):
                self.assertEqual(
                    by_id[component_id]["version"], f"source-offer-sha256:{digest}"
                )
                self.assertIn(digest, closure)
                self.assertIn(
                    "release/COMMUNITY-NONCOMMERCIAL-SOURCE-CLOSURE.md",
                    by_id[component_id]["evidence"],
                )

    def test_v010_release_notes_state_and_legal_boundary(self) -> None:
        notes = (ROOT / "release/radar-puffin-v0.1.0.md").read_text()
        for required in (
            "OTA:", "Initial install:", "Checksums:",
            "both downloaded archives", "available by request", "not part of the normal", "prerelease",
            "CC-BY-NC-SA-4.0", "noncommercial", "ShareAlike",
            "No device was flashed", "owner-device-local",
        ):
            self.assertIn(required, notes)
        normalized_notes = " ".join(notes.split())
        self.assertIn(
            "Final Product commit: see the sanitized release provenance asset",
            normalized_notes,
        )
        self.assertNotIn("PREPARED_NOT_FLASHED", notes)
        boundary = (ROOT / "release/README.md").read_text()
        self.assertIn("Normal public downloads", boundary)
        self.assertIn("Compliance materials", boundary)
        self.assertIn("furnished to recipients on", boundary)
        self.assertIn("signed development OTA", boundary)
        self.assertIn("dev` OTA channel", boundary)
        self.assertIn("never marked `latest`", boundary)
        self.assertNotIn("Hosted main-branch builds may also produce an **unsigned development", boundary)

    def test_documented_good_faith_fpga_record_is_accepted(self) -> None:
        data = json.loads((ROOT / "release/components.json").read_text())
        audio = next(c for c in data["components"] if c["id"] == "mt8163-audio-fpga")
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "release").mkdir()
            (repository / "release/THIRD_PARTY_NOTICES.md").write_text("notices\n")
            (repository / "release/FPGA-PROVENANCE.md").write_text("documented\n")
            catalog = repository / "release/components.json"
            catalog.write_text(json.dumps({"schema_version": 2, "components": [audio]}))
            self.assertEqual(PREPARE_RELEASE.load_components(catalog), [audio])

    def test_documented_good_faith_requires_explicit_hash_contract(self) -> None:
        data = json.loads((ROOT / "release/components.json").read_text())
        audio = next(c for c in data["components"] if c["id"] == "mt8163-audio-fpga")
        audio.pop("known_good_sha256")
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "release").mkdir()
            (repository / "release/THIRD_PARTY_NOTICES.md").write_text("notices\n")
            (repository / "release/FPGA-PROVENANCE.md").write_text("documented\n")
            catalog = repository / "release/components.json"
            catalog.write_text(json.dumps({"schema_version": 2, "components": [audio]}))
            with self.assertRaises(SystemExit) as failure:
                PREPARE_RELEASE.load_components(catalog)
        self.assertIn("documented-good-faith component lacks", str(failure.exception))

    def test_sbom_accepts_documented_good_faith_with_noassertion(self) -> None:
        data = json.loads((ROOT / "release/components.json").read_text())
        audio = next(c for c in data["components"] if c["id"] == "mt8163-audio-fpga")
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "release").mkdir()
            for name in ("THIRD_PARTY_NOTICES.md", "FPGA-PROVENANCE.md"):
                (repository / "release" / name).write_text("test\n")
            catalog = repository / "release/components.json"
            artifacts = repository / "artifacts.json"
            output = repository / "sbom.json"
            catalog.write_text(json.dumps({"schema_version": 2, "components": [audio]}))
            artifacts.write_text(json.dumps([{"name": "boot.img", "sha256": "1" * 64, "size": 4096}]))
            result = subprocess.run([
                sys.executable, str(ROOT / "tools/prepare-sbom.py"),
                "--release-id", "radar-puffin-v0.1.0",
                "--created", "2026-08-08T00:00:00Z",
                "--components", str(catalog),
                "--artifacts", str(artifacts),
                "--output", str(output),
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            package = json.loads(output.read_text())["packages"][0]
            self.assertEqual(package["licenseConcluded"], "NOASSERTION")

    def test_explicit_component_checker_accepts_cleared_catalog(self) -> None:
        result = subprocess.run([
            sys.executable, str(ROOT / "tools/check-release-components.py")
        ], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("component_gate=cleared", result.stdout)
        self.assertIn("release_scope=commercially-unrestricted", result.stdout)

        community = subprocess.run([
            sys.executable, str(ROOT / "tools/check-release-components.py"),
            "--release-scope", "community-noncommercial",
        ], text=True, capture_output=True)
        self.assertEqual(community.returncode, 0, community.stderr)
        self.assertIn("component_count=18", community.stdout)
        self.assertIn("release_scope=community-noncommercial", community.stdout)

    def test_restricted_component_requires_valid_release_scopes(self) -> None:
        def mutate(components):
            wakeword = next(c for c in components if c["id"] == "wakeword-payload")
            wakeword["distribution_scope"] = "separate-payload"
            wakeword["release_status"] = "cleared"
            wakeword["allowed_release_scopes"] = ["commercially-unrestricted"]
        message = self._catalog_failure(mutate)
        self.assertIn("noncommercial component has unsafe release scopes", message)

    def _catalog_failure(self, mutate) -> str:
        data = json.loads((ROOT / "release/components.json").read_text())
        mutate(data["components"])
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / "release").mkdir()
            # Evidence paths are checked only after classification; provide the
            # two public files so the expected policy error remains isolated.
            for name in ("README.md", "THIRD_PARTY_NOTICES.md"):
                (repository / "release" / name).write_text("test\n")
            catalog = repository / "release/components.json"
            catalog.write_text(json.dumps(data))
            with self.assertRaises(SystemExit) as failure:
                PREPARE_RELEASE.load_components(catalog)
        return str(failure.exception)

    def test_redistributed_component_cannot_be_blocked(self) -> None:
        message = self._catalog_failure(
            lambda components: components[0].update(release_status="blocked")
        )
        self.assertIn("redistributed component is not cleared", message)

    def test_nonredistributed_component_cannot_masquerade_as_cleared(self) -> None:
        def mutate(components):
            next(c for c in components if c["id"] == "mt8163-owner-firmware")[
                "release_status"
            ] = "cleared"
        message = self._catalog_failure(mutate)
        self.assertIn("local/external component has unsafe status", message)

    def test_redistributed_component_requires_spdx_conclusion(self) -> None:
        message = self._catalog_failure(
            lambda components: components[0].update(license="NOASSERTION")
        )
        self.assertIn("no SPDX conclusion", message)

    def test_sbom_omits_local_and_external_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts.json"
            provenance = root / "provenance.json"
            output = root / "sbom.json"
            artifacts.write_text(json.dumps([
                {"name": "boot.img", "sha256": "1" * 64, "size": 4096}
            ]))
            write_test_provenance(provenance, "commercially-unrestricted")
            result = subprocess.run([
                sys.executable, str(ROOT / "tools/prepare-sbom.py"),
                "--release-id", "radar-puffin-v0.1.0",
                "--created", "2026-08-08T00:00:00Z",
                "--components", str(ROOT / "release/components.json"),
                "--provenance", str(provenance),
                "--artifacts", str(artifacts),
                "--output", str(output),
            ], check=True, text=True, capture_output=True)
            document = json.loads(output.read_text())
        self.assertIn("package_count=15", result.stdout)
        names = {package["name"] for package in document["packages"]}
        self.assertNotIn("MT8163 connectivity firmware extracted from the owner device", names)
        self.assertNotIn("Amonet/BROM installer integration", names)
        self.assertEqual(len(document["files"]), 1)

    def test_sbom_accepts_full_cleared_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts.json"
            provenance = root / "provenance.json"
            artifacts.write_text(json.dumps([
                {"name": "boot.img", "sha256": "1" * 64, "size": 4096}
            ]))
            write_test_provenance(provenance, "commercially-unrestricted")
            result = subprocess.run([
                sys.executable, str(ROOT / "tools/prepare-sbom.py"),
                "--release-id", "radar-puffin-v0.1.0",
                "--created", "2026-08-08T00:00:00Z",
                "--components", str(ROOT / "release/components.json"),
                "--provenance", str(provenance),
                "--artifacts", str(artifacts),
                "--output", str(root / "sbom.json"),
            ], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("package_count=15", result.stdout)

    def test_sbom_includes_wakeword_only_for_community_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts.json"
            provenance = root / "provenance.json"
            output = root / "sbom.json"
            artifacts.write_text(json.dumps([
                {"name": "boot.img", "sha256": "1" * 64, "size": 4096}
            ]))
            write_test_provenance(provenance, "community-noncommercial")
            result = subprocess.run([
                sys.executable, str(ROOT / "tools/prepare-sbom.py"),
                "--release-id", "radar-puffin-v0.1.0",
                "--created", "2026-08-08T00:00:00Z",
                "--release-scope", "community-noncommercial",
                "--components", str(ROOT / "release/components.json"),
                "--provenance", str(provenance),
                "--artifacts", str(artifacts),
                "--output", str(output),
            ], text=True, capture_output=True)
            names = {
                package["name"] for package in json.loads(output.read_text())["packages"]
            } if output.exists() else set()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("package_count=16", result.stdout)
        self.assertIn(
            "openWakeWord runtime and pretrained Alexa-compatible model", names
        )

    def test_component_gate_rejects_unresolved_redistributed_source_offer(self) -> None:
        catalog = json.loads((ROOT / "release/components.json").read_text())
        component = next(
            item for item in catalog["components"] if item["id"] == "mt8163-audio-fpga"
        )
        component["release_status"] = "cleared"
        component["license"] = "MIT"
        component["download_location"] = "https://example.com/audio-fpga"
        component["source_offer"] = "NOASSERTION"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=ROOT / "release"
        ) as temporary:
            json.dump(catalog, temporary)
            temporary.flush()
            with self.assertRaisesRegex(SystemExit, "source offer is unresolved"):
                PREPARE_RELEASE.load_components(Path(temporary.name))

    def test_sbom_reuses_the_full_component_gate(self) -> None:
        catalog = json.loads((ROOT / "release/components.json").read_text())
        component = next(
            item for item in catalog["components"] if item["id"] == "mt8163-audio-fpga"
        )
        component["release_status"] = "cleared"
        component["license"] = "NOASSERTION"
        component["download_location"] = "NOASSERTION"
        component["source_offer"] = "NOASSERTION"
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", dir=ROOT / "release"
        ) as catalog_file, tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog_file.write(json.dumps(catalog))
            catalog_file.flush()
            artifacts = root / "artifacts.json"
            artifacts.write_text(json.dumps([
                {"name": "boot.img", "sha256": "1" * 64, "size": 4096}
            ]))
            result = subprocess.run([
                sys.executable, str(ROOT / "tools/prepare-sbom.py"),
                "--release-id", "radar-puffin-v0.1.0",
                "--created", "2026-08-08T00:00:00Z",
                "--components", catalog_file.name,
                "--artifacts", str(artifacts),
                "--output", str(root / "sbom.json"),
            ], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public component gate failed", result.stderr)

    def test_sbom_resolves_source_versions_from_release_provenance(self) -> None:
        commits = {
            "product": "1" * 40,
            "kernel": "2" * 40,
            "tooling": "3" * 40,
            "ui": "4" * 40,
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts.json"
            provenance = root / "provenance.json"
            output = root / "sbom.json"
            artifacts.write_text(json.dumps([
                {"name": "boot.img", "sha256": "1" * 64, "size": 4096}
            ]))
            provenance.write_text(json.dumps({
                "release_id": "radar-puffin-v0.1.0",
                "release_scope": "community-noncommercial",
                "sources": {
                    name: {"repository": f"https://github.com/example/{name}", "commit": commit}
                    for name, commit in commits.items()
                },
            }))
            result = subprocess.run([
                sys.executable, str(ROOT / "tools/prepare-sbom.py"),
                "--release-id", "radar-puffin-v0.1.0",
                "--created", "2026-08-08T00:00:00Z",
                "--release-scope", "community-noncommercial",
                "--components", str(ROOT / "release/components.json"),
                "--provenance", str(provenance),
                "--artifacts", str(artifacts),
                "--output", str(output),
            ], text=True, capture_output=True)
            document = json.loads(output.read_text()) if output.exists() else {}
        self.assertEqual(result.returncode, 0, result.stderr)
        by_name = {item["name"]: item for item in document["packages"]}
        self.assertEqual(by_name["LibreEcho product source"]["versionInfo"], commits["product"])
        self.assertEqual(
            by_name["Linux 6.1 kernel, MT8163 product drivers, and embedded firmware lineage"]["versionInfo"],
            commits["kernel"],
        )
        self.assertEqual(
            by_name["LibreEcho Platform and initramfs tooling"]["versionInfo"],
            commits["tooling"],
        )
        self.assertEqual(by_name["LibreEcho UI and service daemons"]["versionInfo"], commits["ui"])



class FeatureStagingIntegrityTests(unittest.TestCase):
    """Exercise the shipped shell; only host paths, mounts and sync are shimmed."""

    def fixture(self, root: Path, *, wrong_size=False, omit_hash=False):
        payload = root / "input.squashfs"
        metadata = root / "input.json"
        payload.write_bytes(b"verified payload fixture")
        metadata.write_bytes(b'{"feature":"assistant","schema":1}\n')
        config = root / "stage.conf"
        config.write_text(
            f"FEATURE_ID=assistant\nPAYLOAD_FILE={payload}\nMANIFEST_FILE={metadata}\n"
            f"PAYLOAD_SHA256={hashlib.sha256(payload.read_bytes()).hexdigest()}\n"
            f"PAYLOAD_SIZE={payload.stat().st_size}\n"
            + ("" if omit_hash else f"MANIFEST_SHA256={hashlib.sha256(metadata.read_bytes()).hexdigest()}\n")
            + f"MANIFEST_SIZE={metadata.stat().st_size + int(wrong_size)}\n"
        )
        dest = root / "features/assistant"
        dest.mkdir(parents=True)
        (dest / "payload.squashfs").write_bytes(b"old payload")
        (dest / "manifest.json").write_bytes(b"old manifest")
        return payload, metadata, config, dest

    def run_stager(self, root: Path, *, corrupt_copy=False, fail_sync=False):
        shim = root / "busybox-test"
        # No command can mount or sync the host. File operations run unchanged
        # in temporary paths against the real BusyBox implementation.
        shim.write_text('''#!/bin/sh
set -eu
case "$1" in
  grep) if [ "${4:-}" = /proc/mounts ]; then exit 0; fi ;;
  mount) exit 0 ;;
  sync) [ "$FAIL_SYNC" = 0 ]; exit $? ;;
esac
if [ -n "$REAL_BUSYBOX" ]; then "$REAL_BUSYBOX" "$@"; else "$@"; fi
if [ "$1" = cp ] && [ "$CORRUPT_COPY" = 1 ]; then
  case "$3" in */manifest.json.new) printf corrupt >> "$3" ;; esac
fi
''')
        shim.chmod(0o755)
        script = INSTALLER.ROOT_FEATURE_STAGER.replace(
            "BB=/bin/busybox", f"BB={shim}"
        ).replace(
            "CONFIG=/tmp/libreecho-feature-stage.conf", f"CONFIG={root / 'stage.conf'}"
        ).replace(
            "DEST=/data/libreecho/features/$FEATURE_ID", f"DEST={root}/features/$FEATURE_ID"
        )
        busybox = shutil.which("busybox")
        interpreter = [busybox, "sh"] if busybox else ["/bin/sh"]
        return subprocess.run(
            [*interpreter, "-c", script], capture_output=True, text=True, timeout=15,
            env={**os.environ, "REAL_BUSYBOX": busybox or "",
                 "FAIL_SYNC": str(int(fail_sync)), "CORRUPT_COPY": str(int(corrupt_copy))},
        )

    def test_valid_pair_is_committed_before_staging_marker_disappears(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload, metadata, config, dest = self.fixture(root)
            expected_payload, expected_metadata = payload.read_bytes(), metadata.read_bytes()
            result = self.run_stager(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("FEATURE_STAGE_OK:assistant", result.stdout)
            self.assertEqual((dest / "payload.squashfs").read_bytes(), expected_payload)
            self.assertEqual((dest / "manifest.json").read_bytes(), expected_metadata)
            self.assertFalse((dest / "staging").exists())
            self.assertFalse(config.exists())

    def test_invalid_manifest_cannot_replace_existing_pair(self):
        for failure in ("hash", "size", "missing-hash", "symlink"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                _, metadata, _, dest = self.fixture(
                    root, wrong_size=failure == "size", omit_hash=failure == "missing-hash")
                if failure == "hash":
                    metadata.write_bytes(b"changed during transport")
                elif failure == "symlink":
                    original = root / "other.json"
                    metadata.rename(original)
                    metadata.symlink_to(original)
                result = self.run_stager(root)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FEATURE_STAGE_MANIFEST_", result.stdout)
                self.assertEqual((dest / "payload.squashfs").read_bytes(), b"old payload")
                self.assertEqual((dest / "manifest.json").read_bytes(), b"old manifest")
                self.assertNotIn("FEATURE_STAGE_OK", result.stdout)

    def test_manifest_copy_corruption_preserves_old_pair_and_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, _, _, dest = self.fixture(root)
            result = self.run_stager(root, corrupt_copy=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FEATURE_STAGE_MANIFEST_COPY_HASH_MISMATCH", result.stdout)
            self.assertEqual((dest / "payload.squashfs").read_bytes(), b"old payload")
            self.assertEqual((dest / "manifest.json").read_bytes(), b"old manifest")
            self.assertTrue((dest / "staging").is_dir())

    def test_sync_failure_keeps_activation_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, _, _, dest = self.fixture(root)
            result = self.run_stager(root, fail_sync=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FEATURE_STAGE_COMMIT_SYNC_FAILED", result.stdout)
            self.assertTrue((dest / "staging").is_dir())
            self.assertNotIn("FEATURE_STAGE_OK", result.stdout)

    def test_host_reads_back_both_files_and_rejects_manifest_mismatch(self):
        for tampered in (False, True):
            with self.subTest(tampered=tampered), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                tag = "radar-puffin-v0.14.0"
                bundle = root / tag / "bundle"
                bundle.mkdir(parents=True)
                payload = bundle / "assistant.squashfs"
                metadata = bundle / "assistant.json"
                payload.write_bytes(b"fixture payload")
                metadata.write_bytes(b'{"fixture":true}')
                feature = {"name": "assistant"}
                for kind, path in (("payload", payload), ("manifest", metadata)):
                    feature[kind] = {"name": path.name, "size": path.stat().st_size,
                                     "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                calls = []
                configs = []
                def command(argv, timeout, *, check=True):
                    calls.append(argv)
                    out = ""
                    if argv[3] == "push" and argv[-1].endswith("stage.conf"):
                        configs.append(Path(argv[-2]).read_text())
                    elif argv[3:5] == ["shell", "sh"]:
                        out = "FEATURE_STAGE_OK:assistant\n"
                    elif argv[3:5] == ["shell", "sha256sum"]:
                        kind = "manifest" if argv[-1].endswith("manifest.json") else "payload"
                        digest = "0" * 64 if tampered and kind == "manifest" else feature[kind]["sha256"]
                        out = f"{digest}  {argv[-1]}\r\n"
                    return subprocess.CompletedProcess(argv, 0, out, "")
                with mock.patch.object(INSTALLER, "_run_command", side_effect=command):
                    if tampered:
                        with self.assertRaisesRegex(INSTALLER.InstallerError, "installed manifest hash mismatch"):
                            INSTALLER.stage_device_features("adb", "SERIAL", root, {"release": tag, "features": [feature]})
                    else:
                        INSTALLER.stage_device_features("adb", "SERIAL", root, {"release": tag, "features": [feature]})
                self.assertIn(f"MANIFEST_SHA256={feature['manifest']['sha256']}\n", configs[0])
                self.assertIn(f"MANIFEST_SIZE={feature['manifest']['size']}\n", configs[0])
                readbacks = [c[-1] for c in calls if c[3:5] == ["shell", "sha256sum"]]
                self.assertEqual(readbacks, ["/data/libreecho/features/assistant/payload.squashfs",
                                            "/data/libreecho/features/assistant/manifest.json"])

    def test_readback_rejects_unrelated_hash_or_extra_output(self):
        feature = {"name": "assistant", "payload": {"sha256": "a" * 64},
                   "manifest": {"sha256": "b" * 64}}
        for output in ("a" * 64 + "  /tmp/other", "noise\n" + "a" * 64 +
                       "  /data/libreecho/features/assistant/payload.squashfs"):
            with self.subTest(output=output), mock.patch.object(
                INSTALLER, "_run_command", return_value=subprocess.CompletedProcess([], 0, output, "")
            ):
                with self.assertRaises(INSTALLER.InstallerError):
                    INSTALLER.verify_device_features("adb", "SERIAL", {"features": [feature]})


if __name__ == "__main__":
    unittest.main()
