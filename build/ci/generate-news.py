#!/usr/bin/env python3
"""Build news-v1.json for the dashboard's LibreEcho News panel (#121).

Merges the newest stable GitHub releases with the curated entries in
news/entries.json, newest first, at most 20 items, and validates the result
with the same rules the device applies (tools/check-news.py) before writing it.

  generate-news.py --releases releases.json --entries news/entries.json --out news-v1.json

releases.json is `gh release list --json tagName,name,publishedAt,isPrerelease,isDraft`
merged with each release's `url` and `body` (CI collects it with an authenticated
token; the device never calls the GitHub API). Prereleases and drafts are skipped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("check_news", ROOT / "tools/check-news.py")
assert _spec and _spec.loader
check_news = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_news)

MAX_RELEASES = 5


def plain_summary(body: str) -> str:
    """First non-heading paragraph of release notes as plain text, <= 400 bytes."""
    text = ""
    for block in re.split(r"\n\s*\n", body or ""):
        block = block.strip()
        if block and not block.startswith("#"):
            text = block
            break
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[`*_>#]", "", text)
    text = " ".join(text.split())
    while len(text.encode()) > 400:
        text = text[:-2].rstrip() + "…"
    return text


def release_items(releases: list[dict]) -> list[dict]:
    stable = [r for r in releases if not r.get("isPrerelease") and not r.get("isDraft")]
    stable.sort(key=lambda r: r["publishedAt"], reverse=True)
    items = []
    for r in stable[:MAX_RELEASES]:
        tag = r["tagName"]
        items.append({
            "id": "release-" + re.sub(r"[^a-z0-9._-]", "-", tag.lower())[:56],
            "type": "release",
            "date": r["publishedAt"][:10],
            "title": (r.get("name") or tag)[:120],
            "summary": plain_summary(r.get("body", "")),
            "url": r.get("url") or f"https://github.com/aslater3/LibreEcho/releases/tag/{tag}",
        })
    return items


def build(releases: list[dict], entries: dict, now: dt.datetime) -> dict:
    if entries.get("schema") != "libreecho-news-entries-v1":
        raise ValueError("news/entries.json has the wrong schema")
    items = release_items(releases) + list(entries.get("entries", []))
    seen, unique = set(), []
    for item in items:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)
    unique.sort(key=lambda i: (i["date"], i["id"]), reverse=True)
    doc = {"schema": "libreecho-news-v1", "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "items": unique[:check_news.MAX_ITEMS]}
    check_news.validate(json.dumps(doc).encode())
    return doc


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--releases", required=True)
    p.add_argument("--entries", default=str(ROOT / "news/entries.json"))
    p.add_argument("--out", required=True)
    a = p.parse_args()
    doc = build(json.loads(Path(a.releases).read_text()), json.loads(Path(a.entries).read_text()),
                dt.datetime.now(dt.timezone.utc))
    Path(a.out).write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"news-v1.json: {len(doc['items'])} item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
