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


PREPARE_RELEASE = load_module("prepare_release", ROOT / "tools/prepare-release.py")


class ComponentGateTests(unittest.TestCase):
    def test_public_catalog_fails_closed_on_unresolved_components(self) -> None:
        with self.assertRaises(SystemExit) as failure:
            PREPARE_RELEASE.load_components(ROOT / "release/components.json")
        message = str(failure.exception)
        for component_id in (
            "core-runtime-closure", "airplay-payload", "stt-payload",
            "tts-payload", "wakeword-payload", "assistant-payload",
        ):
            self.assertIn(component_id, message)
        self.assertNotIn("mt8163-audio-fpga: redistributed component is not cleared", message)

        data = json.loads((ROOT / "release/components.json").read_text())
        self.assertEqual(len(data["components"]), 17)
        audio = next(c for c in data["components"] if c["id"] == "mt8163-audio-fpga")
        self.assertEqual(audio["license"], "NOASSERTION")
        self.assertEqual(audio["release_status"], "documented-good-faith")
        self.assertTrue(audio["included_in_candidate"])
        self.assertEqual(audio["known_good_size"], 30964)
        self.assertEqual(audio["known_good_sha256"], "77a558bacdaaf9e343f02f2d74f27a5f2bb2dc8b6d66cc2499b60ed14ef62fe6")
        self.assertIn("audio-capable candidate", (ROOT / "release/THIRD_PARTY_NOTICES.md").read_text())
        self.assertNotIn("therefore excludes it from public artifacts", (ROOT / "release/THIRD_PARTY_NOTICES.md").read_text())

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
            root = Path(temporary)
            catalog = root / "components.json"
            artifacts = root / "artifacts.json"
            output = root / "sbom.json"
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

    def test_explicit_component_checker_fails_closed(self) -> None:
        result = subprocess.run([
            sys.executable, str(ROOT / "tools/check-release-components.py")
        ], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public component gate failed", result.stderr)
        self.assertNotIn("mt8163-audio-fpga: redistributed component is not cleared", result.stderr)

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
            catalog_data = json.loads((ROOT / "release/components.json").read_text())
            catalog_data["components"] = [
                component for component in catalog_data["components"]
                if component["release_status"] in {"cleared", "not-redistributed"}
            ]
            catalog = root / "components.json"
            catalog.write_text(json.dumps(catalog_data))
            artifacts = root / "artifacts.json"
            output = root / "sbom.json"
            artifacts.write_text(json.dumps([
                {"name": "boot.img", "sha256": "1" * 64, "size": 4096}
            ]))
            result = subprocess.run([
                sys.executable, str(ROOT / "tools/prepare-sbom.py"),
                "--release-id", "radar-puffin-v0.1.0",
                "--created", "2026-08-08T00:00:00Z",
                "--components", str(catalog),
                "--artifacts", str(artifacts),
                "--output", str(output),
            ], check=True, text=True, capture_output=True)
            document = json.loads(output.read_text())
        self.assertIn("package_count=8", result.stdout)
        names = {package["name"] for package in document["packages"]}
        self.assertNotIn("MT8163 connectivity firmware extracted from the owner device", names)
        self.assertNotIn("Amonet/BROM installer integration", names)
        self.assertEqual(len(document["files"]), 1)

    def test_sbom_rejects_full_catalog_while_components_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts.json"
            artifacts.write_text(json.dumps([
                {"name": "boot.img", "sha256": "1" * 64, "size": 4096}
            ]))
            result = subprocess.run([
                sys.executable, str(ROOT / "tools/prepare-sbom.py"),
                "--release-id", "radar-puffin-v0.1.0",
                "--created", "2026-08-08T00:00:00Z",
                "--components", str(ROOT / "release/components.json"),
                "--artifacts", str(artifacts),
                "--output", str(root / "sbom.json"),
            ], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("redistributed component is not cleared", result.stderr)


if __name__ == "__main__":
    unittest.main()
