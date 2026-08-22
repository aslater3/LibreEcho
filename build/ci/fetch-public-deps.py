#!/usr/bin/env python3
"""Fail-closed validation for the public dependency inventory."""
from __future__ import annotations
import hashlib
import json
import re
import subprocess
import shutil
import tarfile
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
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
        if item["kind"] == "source-git":
            if not item["url"].startswith("https://"):
                raise ValueError(f"source-git input is not fetchable: {item['name']}")
            if not COMMIT.fullmatch(item.get("commit", "")):
                raise ValueError(f"source-git input is not pinned: {item['name']}")
            if item["redistribution"] != "source-git-pinned":
                raise ValueError(f"source-git input has invalid redistribution: {item['name']}")
        if item["redistribution"] == "cleared":
            if not item["url"].startswith("https://") or not SHA.fullmatch(item["sha256"]):
                raise ValueError(f"cleared input is not fetchable: {item['name']}")
    return data


def fetch(record: dict, destination: Path) -> Path:
    if record["redistribution"] != "cleared":
        raise ValueError(f"input is not cleared: {record['name']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = [
        "curl", "--fail", "--location", "--ipv4", "--retry", "5",
        "--retry-all-errors", "--connect-timeout", "30", "--max-time", "600",
        "--user-agent", "LibreEcho-public-build/1", "--output", str(destination), record["url"]
    ]
    subprocess.run(request, check=True)
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
    "stt-license": "README.md",
    "wakeword-alexa": "alexa_v0.1.onnx",
    "wakeword-embedding": "embedding_model.onnx",
    "wakeword-melspectrogram": "melspectrogram.onnx",
    "speexdsp": "speexdsp-SpeexDSP-1.2.1.tar.gz",
    "tinyalsa": "tinyalsa-e43025bbf702eb7dd8edd48c1eb50530c60f1de8.tar.gz",
    "nqptp": "nqptp-1.2.8.tar.gz",
    "shairport-sync": "shairport-sync-5.1.tar.gz",
    "ca-certificates": "ca-certificates-20260601.crt",
    "ca-certificates-notice": "ca-certificates-20260601.copyright",
    "plistutil-package": "host-packages/libplist-utils.deb",
    "libplist-runtime-package": "host-packages/libplist-runtime.deb",
    "libplist-source": "host-tools/source/libplist_2.3.0.orig.tar.bz2",
    "libplist-debian-source": "host-tools/source/libplist_2.3.0-1~exp2build2.debian.tar.xz",
    "libplist-source-descriptor": "host-tools/source/libplist_2.3.0-1~exp2build2.dsc",
}

RUNNER_SOURCES = {
    "ca-certificates": Path("/etc/ssl/certs/ca-certificates.crt"),
    "ca-certificates-notice": Path("/usr/share/doc/ca-certificates/copyright"),
}


def stage(inventory: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for record in inventory["inputs"]:
        if record["kind"] == "generated-runner-input":
            relative = NAMES[record["name"]]
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = RUNNER_SOURCES[record["name"]]
            if not source.is_file():
                raise FileNotFoundError(f"hosted runner input is missing: {source}")
            shutil.copy2(source, destination)
            continue
        if record["kind"] == "source-git":
            checkout = output / record["name"]
            subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", record["url"], str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "checkout", "--quiet", record["commit"]], check=True)
            continue
        if record["kind"] == "source-archive-tree":
            archive_path = output / (record["name"] + ".archive")
            fetch(record, archive_path)
            destination = output / record["name"]
            destination.mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive_path) as archive:
                archive.extractall(destination, filter="data")
            archive_path.unlink()
            children = list(destination.iterdir())
            if len(children) == 1 and children[0].is_dir():
                nested = children[0]
                for child in nested.iterdir():
                    child.rename(destination / child.name)
                nested.rmdir()
            continue
        if record["redistribution"] != "cleared":
            continue
        relative = NAMES.get(record["name"], record["url"].rsplit("/", 1)[-1])
        fetch(record, output / relative)
    packages = output / "host-packages"
    for package in (packages / "libplist-utils.deb", packages / "libplist-runtime.deb"):
        target = output / "host-tools"
        target.mkdir(exist_ok=True)
        subprocess.run(["dpkg-deb", "-x", str(package), str(target)], check=True)
    (output / "host-tools/bin").mkdir(parents=True, exist_ok=True)
    (output / "host-tools/lib").mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", str(output / "host-tools/usr/bin/plistutil"), str(output / "host-tools/bin/plistutil")], check=True)
    runtime = next((output / "host-tools/usr/lib").rglob("libplist-2.0.so.4*"))
    subprocess.run(["cp", str(runtime), str(output / "host-tools/lib/libplist-2.0.so.4")], check=True)
    source_root = output / "host-tools/source"
    (output / "host-tools/share/libplist").mkdir(parents=True, exist_ok=True)
    copyright_file = next((output / "host-tools/usr/share/doc").rglob("copyright"))
    subprocess.run(["cp", str(copyright_file), str(output / "host-tools/share/libplist/copyright")], check=True)
    with tarfile.open(source_root / "libplist_2.3.0.orig.tar.bz2") as archive:
        member = next(x for x in archive.getmembers() if x.name.endswith("/COPYING.LESSER"))
        target = output / "host-tools/share/libplist/COPYING.LESSER"
        target.write_bytes(archive.extractfile(member).read())
    manifest = {
        "schema": 1, "architecture": "amd64", "source_package": "libplist",
        "package_version": "2.3.0-1~exp2build2", "plistutil_package": "libplist-utils",
        "plistutil_package_sha256": "8a5c32845d9a33a052ff82412d77a2831f3f77672024610044ee8aa06d3604fa",
        "plistutil_sha256": hashlib.sha256((output / "host-tools/bin/plistutil").read_bytes()).hexdigest(),
        "runtime_package": "libplist-2.0-4", "runtime_package_sha256": "e425c79a3e6e336ce05be7ad7d4171d0a956437cd69d13f27e0df98e272a6f26",
        "libplist_sha256": hashlib.sha256((output / "host-tools/lib/libplist-2.0.so.4").read_bytes()).hexdigest(),
        "license": "LGPL-2.1-or-later", "source": "https://archive.ubuntu.com/ubuntu/pool/main/libp/libplist/", "ubuntu_suite": "noble"
    }
    (output / "host-tools/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    sums = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            sums.append(f"{digest}  {path.relative_to(output)}")
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")


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
