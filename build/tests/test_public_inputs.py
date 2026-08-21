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


if __name__ == "__main__":
    unittest.main()
