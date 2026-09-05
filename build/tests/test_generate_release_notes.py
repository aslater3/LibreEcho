#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location(
    "generate_release_notes", ROOT / "build/ci/generate-release-notes.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseNotesTests(unittest.TestCase):
    def test_render_contains_all_heads_and_required_license_disclosures(self) -> None:
        heads = {name: f"{index:x}" * 40 for index, name in enumerate(MODULE.REPOSITORIES, 1)}
        bases = {name: "release/0.13.8" for name in MODULE.REPOSITORIES}
        changes = {
            name: [{"sha": "a" * 40, "message": f"fix: update {name}"}]
            for name in MODULE.REPOSITORIES
        }
        rendered = MODULE.render("0.14.0", heads, bases, changes)
        for name in MODULE.REPOSITORIES:
            self.assertIn(name, rendered)
        self.assertIn("CC-BY-NC-SA-4.0", rendered)
        self.assertIn("noncommercial", rendered)
        self.assertIn("ShareAlike", rendered)
        self.assertIn("CC-BY-SA-4.0", rendered)
        self.assertIn("fix: update Platform", rendered)

    def test_render_marks_empty_component_range(self) -> None:
        heads = {name: "a" * 40 for name in MODULE.REPOSITORIES}
        bases = {name: None for name in MODULE.REPOSITORIES}
        changes = {name: [] for name in MODULE.REPOSITORIES}
        rendered = MODULE.render("0.14.0", heads, bases, changes)
        self.assertEqual(rendered.count("Changes: none in the selected comparison range"), 4)

    def test_generated_ledger_is_appended_to_authored_notes(self) -> None:
        authored = "# LibreEcho radar-puffin v0.14.0\n\nMaintained release highlights.\n"
        generated = "# LibreEcho radar-puffin v0.14.0\n\nGenerated intro.\n\n## Release identity\n\n- exact heads\n"
        merged = MODULE.merge_authored_notes(authored, generated)
        self.assertTrue(merged.startswith(authored.rstrip()))
        self.assertEqual(merged.count("# LibreEcho radar-puffin v0.14.0"), 1)
        self.assertIn("## Generated exact-source ledger", merged)
        self.assertIn("- exact heads", merged)


if __name__ == "__main__":
    unittest.main()
