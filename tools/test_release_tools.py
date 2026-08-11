#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


class PublicMetadataTests(unittest.TestCase):
    def test_versioned_source_url_is_not_an_ipv4_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.md").write_text(
                "https://launchpad.net/source/2025.10.07-0ubuntu1~24.04.1/archive.tar.xz"
            )
            self.assertEqual(PUBLIC_METADATA.violations(root), [])

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


class ComponentGateTests(unittest.TestCase):
    def test_public_catalog_scopes_noncommercial_wakeword(self) -> None:
        unrestricted = PREPARE_RELEASE.load_components(
            ROOT / "release/components.json", "commercially-unrestricted"
        )
        community = PREPARE_RELEASE.load_components(
            ROOT / "release/components.json", "community-noncommercial"
        )
        data = json.loads((ROOT / "release/components.json").read_text())
        self.assertEqual(len(data["components"]), 17)
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
            "PREPARED_NOT_FLASHED", "prerelease", "CC-BY-NC-SA-4.0",
            "noncommercial", "ShareAlike",
            "No device was flashed", "owner-device-local",
        ):
            self.assertIn(required, notes)
        normalized_notes = " ".join(notes.split())
        self.assertIn(
            "Final Product commit: see the sanitized release provenance asset",
            normalized_notes,
        )
        self.assertNotIn(
            "Product: `c0f3dc3ad89c3a739c0c477b3f98e26acc9a4d70`",
            notes,
        )

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
        self.assertIn("component_count=17", community.stdout)
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
        self.assertIn("package_count=14", result.stdout)
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
        self.assertIn("package_count=14", result.stdout)

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
        self.assertIn("package_count=15", result.stdout)
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


if __name__ == "__main__":
    unittest.main()
