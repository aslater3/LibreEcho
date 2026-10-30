#!/usr/bin/env python3
"""Validate news-v1.json with the rules LibreEcho-UI src/news.c applies on the device (#121).

usage: check-news.py news-v1.json     exit 0 valid, 1 invalid (reason on stderr)
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlsplit

MAX_BYTES = 32768
MAX_ITEMS = 20
TYPES = {"release", "progress", "announcement", "security"}
HOSTS = {"github.com", "libreecho.org", "dl.libreecho.org"}
ID = re.compile(r"^[a-z0-9._-]{1,64}$")
DATE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def _no_duplicates(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError(f"duplicate key in {keys}")
    return dict(pairs)


def _plain(value: str, limit: int, field: str) -> None:
    if not isinstance(value, str) or len(value.encode()) > limit or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        raise ValueError(f"{field} must be plain single-line text of at most {limit} bytes")


def validate(raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise ValueError(f"document is {len(raw)} bytes, over {MAX_BYTES}")
    doc = json.loads(raw, object_pairs_hook=_no_duplicates)
    if not isinstance(doc, dict) or doc.get("schema") != "libreecho-news-v1" or not isinstance(doc.get("items"), list):
        raise ValueError("schema must be libreecho-news-v1 with an items array")
    if len(doc["items"]) > MAX_ITEMS:
        raise ValueError(f"more than {MAX_ITEMS} items")
    ids = set()
    for item in doc["items"]:
        if not isinstance(item, dict):
            raise ValueError("every item must be an object")
        for field in ("id", "type", "date", "title", "summary", "url"):
            if not isinstance(item.get(field), str):
                raise ValueError(f"item is missing {field}")
        if not ID.match(item["id"]) or item["id"] in ids:
            raise ValueError(f"bad or duplicate id {item['id']!r}")
        ids.add(item["id"])
        if not DATE.match(item["date"]):
            raise ValueError(f"bad date {item['date']!r}")
        if not item["title"]:
            raise ValueError("empty title")
        _plain(item["title"], 120, "title")
        _plain(item["summary"], 400, "summary")
        url = item["url"]
        parts = urlsplit(url)
        if (len(url) > 256 or parts.scheme != "https" or parts.hostname not in HOSTS or parts.port or
                parts.username or any(c in url for c in ' "\\<>') or any(ord(c) < 0x21 or ord(c) >= 0x7f for c in url)):
            raise ValueError(f"url must be https on {sorted(HOSTS)}: {url!r}")
        if item.get("severity", "") not in ("", "info", "important"):
            raise ValueError("severity must be info or important")
        if item["type"] not in TYPES:
            print(f"note: item {item['id']} has a type the device will skip: {item['type']}", file=sys.stderr)
    return doc


def main() -> int:
    try:
        validate(open(sys.argv[1], "rb").read())
    except (ValueError, json.JSONDecodeError) as error:
        print(f"invalid news feed: {error}", file=sys.stderr)
        return 1
    print("news feed: valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
