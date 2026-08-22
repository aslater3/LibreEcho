#!/usr/bin/env python3
"""Fail-closed validation for the public dependency inventory."""
from __future__ import annotations
import hashlib
import json
import re
import urllib.request
import subprocess
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{64}$")
SCHEMA = "libreecho-public-inputs-v1"


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA or not isinstance(data.get("inputs"), list):
        raise ValueError("invalid public input schema")
    names = set()
    for item in data["inputs"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("malformed input record")
        if item["name"] in names:
            raise ValueError("duplicate input name")
        names.add(item["name"])
        for key in ("url", "sha256", "kind", "license", "redistribution"):
            if not isinstance(item.get(key), str):
                raise ValueError(f"missing input field: {key}")
        if item["redistribution"] == "cleared":
            if not item["url"].startswith("https://") or not SHA.fullmatch(item["sha256"]):
                raise ValueError(f"cleared input is not fetchable: {item['name']}")
    return data


def fetch(record: dict, destination: Path) -> Path:
    if record["redistribution"] != "cleared":
        raise ValueError(f"input is not cleared: {record['name']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(record["url"], timeout=30) as source, destination.open("wb") as target:
        while block := source.read(1024 * 1024):
            target.write(block)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != record["sha256"]:
        raise ValueError(f"input digest mismatch: {record['name']}")
    return destination


NAMES = {
    "tts-model": "northern-male.optimized.onnx",
    "tts-model-config": "northern-male.optimized.onnx.json",
    "stt-encoder": "encoder-epoch-99-avg-1.int8.onnx",
    "stt-decoder": "decoder-epoch-99-avg-1.int8.onnx",
    "stt-joiner": "joiner-epoch-99-avg-1.int8.onnx",
    "stt-tokens": "tokens.txt",
    "wakeword-alexa": "alexa_v0.1.onnx",
    "wakeword-embedding": "embedding_model.onnx",
    "wakeword-melspectrogram": "melspectrogram.onnx",
    "plistutil-package": "host-packages/libplist-utils.deb",
    "libplist-runtime-package": "host-packages/libplist-runtime.deb",
}


def stage(inventory: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for record in inventory["inputs"]:
        if record["redistribution"] != "cleared":
            continue
        relative = NAMES.get(record["name"], record["url"].rsplit("/", 1)[-1])
        fetch(record, output / relative)
    packages = output / "host-packages"
    for package in (packages / "libplist-utils.deb", packages / "libplist-runtime.deb"):
        target = output / "host-tools"
        target.mkdir(exist_ok=True)
        subprocess.run(["dpkg-deb", "-x", str(package), str(target)], check=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-generated", action="store_true")
    args = parser.parse_args()
    data = load(args.inventory)
    blocked = [x["name"] for x in data["inputs"] if x["redistribution"].startswith(("blocked-private", "requires-"))]
    if not args.allow_generated:
        blocked += [x["name"] for x in data["inputs"] if x["redistribution"].startswith("blocked-generation")]
    if blocked:
        raise SystemExit("PUBLIC_INPUTS_BLOCKED: " + ",".join(blocked))
    if args.output:
        stage(data, args.output)
    print(f"public_inputs=PASS count={len(data['inputs'])}")
