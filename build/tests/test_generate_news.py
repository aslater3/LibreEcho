#!/usr/bin/env python3
"""LibreEcho News feed generator and validator (#121); rules match LibreEcho-UI src/news.c."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]


def load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = load("generate_news", "build/ci/generate-news.py")
CHECK = load("check_news", "tools/check-news.py")
NOW = dt.datetime(2026, 10, 18, tzinfo=dt.timezone.utc)


def release(tag, date, pre=False, body="Notes."):
    return {"tagName": tag, "name": f"LibreEcho {tag}", "publishedAt": f"{date}T10:00:00Z",
            "isPrerelease": pre, "isDraft": False, "url": f"https://github.com/aslater3/LibreEcho/releases/tag/{tag}",
            "body": body}


class GenerateNewsTests(unittest.TestCase):
    def entries(self, *items):
        return {"schema": "libreecho-news-entries-v1", "entries": list(items)}

    def test_stable_releases_and_entries_newest_first(self):
        doc = GEN.build([release("radar-puffin-v0.13.9", "2026-09-01"),
                         release("radar-puffin-build-123", "2026-10-17", pre=True),
                         release("radar-puffin-v0.14.0", "2026-10-16",
                                 body="## Highlights\n\nThe **0.14** release with [docs](https://x).\n\nMore")],
                        self.entries({"id": "progress-0-15", "type": "progress", "date": "2026-10-18",
                                      "title": "0.15", "summary": "", "url": "https://github.com/aslater3/LibreEcho/releases"}),
                        NOW)
        ids = [i["id"] for i in doc["items"]]
        self.assertEqual(ids, ["progress-0-15", "release-radar-puffin-v0.14.0", "release-radar-puffin-v0.13.9"])
        self.assertEqual(doc["items"][1]["summary"], "The 0.14 release with docs.")
        self.assertEqual(doc["schema"], "libreecho-news-v1")

    def test_at_most_twenty_items(self):
        entries = [{"id": f"p{i:02d}", "type": "progress", "date": "2026-10-01", "title": "T", "summary": "",
                    "url": "https://libreecho.org/x"} for i in range(25)]
        self.assertEqual(len(GEN.build([], self.entries(*entries), NOW)["items"]), 20)

    def test_invalid_entry_fails_the_build(self):
        with self.assertRaises(ValueError):
            GEN.build([], self.entries({"id": "x", "type": "progress", "date": "2026-10-01", "title": "T",
                                        "summary": "", "url": "https://evil.example/x"}), NOW)

    def test_long_summary_is_trimmed_to_400_bytes(self):
        doc = GEN.build([release("v1", "2026-01-01", body="word " * 200)], self.entries(), NOW)
        self.assertLessEqual(len(doc["items"][0]["summary"].encode()), 400)


class CheckNewsTests(unittest.TestCase):
    def valid(self, **change):
        item = {"id": "a", "type": "release", "date": "2026-10-16", "title": "T", "summary": "S",
                "url": "https://github.com/aslater3/LibreEcho/releases"}
        item.update(change)
        return json.dumps({"schema": "libreecho-news-v1", "items": [item]}).encode()

    def test_device_rules(self):
        CHECK.validate(self.valid())
        for change in ({"url": "https://evil.example/x"}, {"url": "http://github.com/x"},
                       {"url": "https://github.com.evil.example/x"}, {"date": "2026-13-01"},
                       {"id": "Upper"}, {"title": "a\nb"}, {"title": ""}, {"severity": "panic"},
                       {"title": "x" * 121}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                CHECK.validate(self.valid(**change))
        with self.assertRaises(ValueError):
            CHECK.validate(b'{"schema":"libreecho-news-v1","schema":"libreecho-news-v1","items":[]}')
        with self.assertRaises(ValueError):
            CHECK.validate(b'{"schema":"libreecho-news-v2","items":[]}')
        with self.assertRaises(ValueError):
            CHECK.validate(b" " * 32769)

    def test_repository_entries_are_valid(self):
        GEN.build([], json.loads((ROOT / "news/entries.json").read_text()), NOW)


if __name__ == "__main__":
    unittest.main()
