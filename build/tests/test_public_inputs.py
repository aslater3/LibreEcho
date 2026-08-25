#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
from importlib.util import module_from_spec, spec_from_file_location

def load(name, path):
    spec = spec_from_file_location(name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

module = load("fetch_public_deps", ROOT / "build/ci/fetch-public-deps.py")


class PublicInputTests(unittest.TestCase):
    def test_inventory_is_closed_and_contains_blockers(self):
        data = module.load(ROOT / "build/inputs/public-inputs.json")
        self.assertEqual(data["schema"], "libreecho-public-inputs-v1")
        self.assertTrue(any(x["redistribution"] != "cleared" for x in data["inputs"]))

    def test_cleared_records_require_digest_and_https(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inputs.json"
            path.write_text(json.dumps({"schema": module.SCHEMA, "inputs": [{"name":"x","url":"http://bad","sha256":"","kind":"archive","license":"MIT","redistribution":"cleared"}]}))
            with self.assertRaises(ValueError): module.load(path)

    def test_blocked_input_cannot_be_fetched(self):
        with self.assertRaises(ValueError):
            module.fetch({"name":"private","url":"","sha256":"","redistribution":"blocked-private","kind":"model","license":""}, Path("/tmp/nope"))

    def test_inventory_entrypoint_fails_closed_when_blocked(self):
        data = module.load(ROOT / "build/inputs/public-inputs.json")
        self.assertEqual(data["status"], "partially-cleared")
        self.assertTrue(any(item["redistribution"].startswith("blocked-") for item in data["inputs"]))
        firmware = next(item for item in data["inputs"] if item["name"] == "owner-local-connectivity-firmware")
        self.assertEqual(firmware["kind"], "runtime-import-contract")
        self.assertIn("bytes-never-uploaded", firmware["redistribution"])

    def test_ota_signing_wheel_closure_is_reviewed_and_pinned(self):
        data = module.load(ROOT / "build/inputs/public-inputs.json")
        records = {item["name"]: item for item in data["inputs"]}
        expected = {
            "ota-signing-pynacl-wheel": "06b8f6fa7f5de8d5d2f7573fe8c863c051225a27b61e6860fd047b1775807858",
            "ota-signing-cffi-wheel": "610faea79c43e44c71e1ec53a554553fa22321b65fae24889706c0a84d4ad86d",
            "ota-signing-pycparser-wheel": "c3702b6d3dd8c7abc1afa565d7e63d53a1d0bd86cdc24edd75470f4de499cfcc",
            "ota-signing-requirements": "1a3bef434d882d4c3aa45c75491452b02934a4f405119a41f43a2cbf0b866530",
        }
        for name, digest in expected.items():
            self.assertEqual(records[name]["kind"], "reviewed-vendored-input")
            self.assertEqual(records[name]["redistribution"], "reviewed-vendored")
            self.assertEqual(records[name]["sha256"], digest)
            self.assertTrue(records[name]["url"].startswith("vendored://reviewed/python-wheels/"))

    def test_neural_source_pins_are_explicit_and_validated(self):
        data = module.load(ROOT / "build/inputs/public-inputs.json")
        records = {item["name"]: item for item in data["inputs"]}
        self.assertEqual(
            records["onnxruntime-source"]["commit"],
            "8f0278c77bf44b0cc83c098c6c722b92a36ac4b5",
        )
        self.assertEqual(
            records["sherpa-onnx-source"]["commit"],
            "546df6f963ae719dddd8b8d10749e9d9086b0d86",
        )
        for name in ("onnxruntime-source", "sherpa-onnx-source"):
            self.assertTrue(records[name]["url"].startswith("https://"))
            self.assertRegex(records[name]["commit"], r"^[0-9a-f]{40}$")
            self.assertEqual(records[name]["kind"], "source-git")
            self.assertEqual(records[name]["redistribution"], "source-git-pinned")

    def test_source_git_records_require_a_pinned_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inputs.json"
            path.write_text(json.dumps({"schema": module.SCHEMA, "inputs": [{
                "name": "x", "url": "https://example.invalid/x.git",
                "commit": "not-a-commit", "sha256": "", "kind": "source-git",
                "license": "MIT", "redistribution": "source-git-pinned",
            }]}))
            with self.assertRaises(ValueError):
                module.load(path)


if __name__ == "__main__":
    unittest.main()
