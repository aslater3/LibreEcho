#!/usr/bin/env python3
"""LibreEcho News feed generator and validator (#121); rules match LibreEcho-UI src/news.c."""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import textwrap
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


WORKFLOW = ROOT / ".github/workflows/publish-news.yml"


def job(text: str, name: str) -> str:
    body = text.split(f"\n  {name}:\n", 1)[1]
    return re.split(r"\n  [a-z][a-z0-9-]*:\n", body, maxsplit=1)[0]


def step(text: str, name: str) -> str:
    return text.split(f"- name: {name}\n", 1)[1].split("\n      - ", 1)[0]


class PublishNewsWorkflowTests(unittest.TestCase):
    """The workflow that publishes the device's GitHub fallback URL."""

    def setUp(self):
        self.text = WORKFLOW.read_text()
        self.build = job(self.text, "build-feed")
        self.publish = job(self.text, "publish-feed")

    def test_triggers_follow_the_product_publisher(self):
        publisher = (ROOT / ".github/workflows/publish-release.yml").read_text().splitlines()[0]
        self.assertEqual(publisher, "name: Publish Product release")
        self.assertIn("workflow_run:\n    workflows: ['Publish Product release']\n    types: [completed]", self.text)
        self.assertIn("\n  workflow_dispatch:\n", self.text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.build)

    def test_write_permission_only_on_the_upload_job(self):
        self.assertIn("\npermissions:\n  contents: read\n", self.text)
        self.assertEqual(self.text.count("contents: write"), 1)
        self.assertIn("permissions:\n      contents: write", self.publish)
        self.assertIn("permissions:\n      contents: read", self.build)
        self.assertNotIn("gh release upload", self.build)
        self.assertNotIn("--method POST", self.build)

    def test_actions_are_pinned_by_commit(self):
        uses = re.findall(r"uses: (\S+)", self.text)
        self.assertTrue(uses)
        for ref in uses:
            self.assertRegex(ref, r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
        self.assertEqual(self.text.count("persist-credentials: false"), 2)

    def test_feed_is_validated_before_it_is_uploaded(self):
        generate = self.build.index("build/ci/generate-news.py")
        check = self.build.index("tools/check-news.py")
        artifact = self.build.index("actions/upload-artifact@")
        self.assertLess(generate, check)
        self.assertLess(check, artifact)
        upload = step(self.publish, "Upload news-v1.json to the news-feed release")
        self.assertLess(upload.index('python3 tools/check-news.py "$feed"'), upload.index("gh release upload"))
        self.assertLess(upload.index("gh release upload"), upload.index("test \"$got\" = \"$want\""))

    def test_uploads_news_v1_to_the_news_feed_release(self):
        self.assertIn("NEWS_TAG: news-feed\n", self.text)
        self.assertIn("NEWS_ASSET: news-v1.json\n", self.text)
        upload = step(self.publish, "Upload news-v1.json to the news-feed release")
        self.assertIn('gh release upload "$NEWS_TAG" "$feed" --repo "$GITHUB_REPOSITORY" --clobber', upload)
        # Created as a prerelease that is never latest: not firmware, not news.
        self.assertIn("-F draft=false -F prerelease=true -f make_latest=false", upload)
        self.assertIn(".draft == false and .prerelease == true", upload)
        verify = step(self.publish, "Verify the device URLs")
        self.assertIn("https://github.com/$GITHUB_REPOSITORY/releases/download/$NEWS_TAG/$NEWS_ASSET", verify)
        self.assertIn("https://dl.libreecho.org/news/$NEWS_ASSET", verify)

    def test_collected_releases_feed_the_generator(self):
        """Run the workflow's own collection step against a stubbed GitHub API page."""
        script = step(self.build, "Collect published releases").split("run: |\n", 1)[1]
        api = [
            {"tag_name": "radar-puffin-v0.14.4", "name": "LibreEcho radar-puffin-v0.14.4",
             "published_at": "2026-10-22T10:00:00Z", "prerelease": False, "draft": False,
             "html_url": "https://github.com/aslater3/LibreEcho/releases/tag/radar-puffin-v0.14.4",
             "body": "## Notes\n\nLibreEcho 0.14.4 fixes things."},
            {"tag_name": "news-feed", "name": "LibreEcho News feed (not a firmware release)",
             "published_at": "2026-10-29T10:00:00Z", "prerelease": True, "draft": False,
             "html_url": "https://github.com/aslater3/LibreEcho/releases/tag/news-feed", "body": None},
            {"tag_name": "radar-puffin-v0.14.5", "name": None, "published_at": None,
             "prerelease": False, "draft": True, "html_url": "https://github.com/x", "body": None},
        ]
        stub = 'gh() { local f=; while [ $# -gt 0 ]; do [ "$1" = --jq ] && f="$2"; shift; done; jq -c "$f" "$API"; }\n'
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "api.json").write_text(json.dumps(api))
            env = {**os.environ, "API": str(Path(tmp) / "api.json"), "GITHUB_REPOSITORY": "aslater3/LibreEcho"}
            result = subprocess.run(["bash", "-c", stub + textwrap.dedent(script)], cwd=tmp, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            releases = json.loads((Path(tmp) / "releases.json").read_text())
        self.assertEqual([r["tagName"] for r in releases], ["radar-puffin-v0.14.4", "news-feed", "radar-puffin-v0.14.5"])
        doc = GEN.build(releases, self.entries_fixture(), NOW)
        self.assertEqual([i["id"] for i in doc["items"]], ["release-radar-puffin-v0.14.4"])
        self.assertEqual(doc["items"][0]["summary"], "LibreEcho 0.14.4 fixes things.")

    @staticmethod
    def entries_fixture():
        return {"schema": "libreecho-news-entries-v1", "entries": []}

    def test_release_gate_covers_the_workflow(self):
        gate = (ROOT / ".github/workflows/release-gate.yml").read_text()
        self.assertEqual(gate.count("- '.github/workflows/publish-news.yml'"), 2)


if __name__ == "__main__":
    unittest.main()
