"""Combine independently verified target preparations without changing tags."""
from __future__ import annotations

import hashlib
import os
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from target_registry import DEFAULT, asset_prefix, descriptor_sha256, parse_targets


def read_kv(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def discover_runs(root):
    runs = [p.parent for p in root.rglob("CURRENT.candidate")]
    if not runs or any(p.is_symlink() for p in runs):
        raise ValueError("missing or unsafe target runs")
    result = {}
    for run in runs:
        candidate = read_kv(run / "CURRENT.candidate")
        target = candidate.get("board", DEFAULT)
        parse_targets([target])
        if target in result:
            raise ValueError("duplicate target candidate")
        result[target] = run
    return result


def _link_or_copy(source, destination):
    # Hard links keep multi-hundred-MiB runs cheap; children only read them.
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def assert_parity(runs):
    reference = next(iter(runs.values()))
    sources = (reference / "release-source-commits.txt").read_bytes()
    shared = ["zImage", "System.map", "kernel.config", "libreecho-radar-puffin.dtb"]
    shared += [f"features/{f}.{s}" for f in ("airplay2", "assistant", "stt", "tts", "wakeword")
               for s in ("squashfs", "manifest.json")]
    for run in runs.values():
        if (run / "release-source-commits.txt").read_bytes() != sources:
            raise ValueError("combined release source sets differ")
        for name in shared:
            a, b = reference / name, run / name
            if (a.is_symlink() or b.is_symlink() or not a.is_file() or not b.is_file()
                    or hashlib.sha256(a.read_bytes()).digest() != hashlib.sha256(b.read_bytes()).digest()):
                raise ValueError("combined release code parity mismatch or missing shared input: " + name)


def prepare_if_combined(args, script):
    runs = discover_runs(args.artifact_root)
    requests = list(args.artifact_root.rglob("release-request.json"))
    if len(requests) > 1:
        raise ValueError("artifact has duplicate release requests")
    if requests:
        selected = json.loads(requests[0].read_text()).get("targets")
        if selected is not None and set(parse_targets(selected)) != set(runs):
            raise ValueError("release target set differs from candidate set")
    if len(runs) == 1:
        return None
    if len(requests) != 1:
        raise ValueError("combined artifact requires one product-wide release request")
    request = json.loads(requests[0].read_text())
    targets = parse_targets(request.get("targets", []))
    if set(runs) != set(targets):
        raise ValueError("release target set differs from candidate set")
    assert_parity(runs)
    output = args.output_dir.resolve()
    if output.exists():
        raise ValueError("combined release output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Radar first pins the unchanged product tag; all other preparations use it.
    ordered = sorted(targets, key=lambda t: (t != DEFAULT, t))
    tag = None
    metadata = {}
    with tempfile.TemporaryDirectory(prefix="combined-release-", dir=output.parent) as tmp:
        stage = Path(tmp)
        for target in ordered:
            run = runs[target]
            # Both lanes: the hosted build stores the product-wide request
            # inside one run, so each per-target child gets an isolated copy
            # carrying a single-target request. The original artifact and
            # product-wide request remain unchanged and are checked above.
            child_run = stage / (target + "-run")
            shutil.copytree(run, child_run, copy_function=_link_or_copy)
            child_request_path = child_run / "release-request.json"
            # Never write through a hard link into the original artifact.
            child_request_path.unlink(missing_ok=True)
            child_request = {**request, "targets": [target]}
            child_request_path.write_text(json.dumps(child_request))
            run = child_run
            child_out = stage / (target + "-assets")
            command = [sys.executable, str(script), "--artifact-root", str(run),
                       "--output-dir", str(child_out), "--product-commit", args.product_commit,
                       "--target", target]
            if script.name == "prepare-dev-release.py":
                command += ["--release-kind", args.release_kind]
                if tag:
                    command += ["--combined-release-tag", tag]
            else:
                for field in ("product_root", "release_version", "release_notes", "amonet_repository", "amonet_tag", "amonet_commit"):
                    command += ["--" + field.replace("_", "-"), str(getattr(args, field))]
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise ValueError(f"{target} release preparation failed: {result.stderr}")
            values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            if tag is None:
                tag = values["release_tag"]
            if values["release_tag"] != tag:
                raise ValueError("combined release tag mismatch")
            metadata[target] = values
        output.mkdir()
        for target in ordered:
            for source in (stage / (target + "-assets")).iterdir():
                destination = output / source.name
                if destination.exists():
                    raise ValueError("combined asset name collision")
                shutil.copyfile(source, destination)
    assert tag is not None
    index = {"schema": "libreecho-combined-release-v1", "release": tag, "targets": [
        {"board": t, "prefix": asset_prefix(tag, t), "target_descriptor_sha256": descriptor_sha256(t)}
        for t in ordered]}
    (output / ("libreecho-" + tag + "-targets.json")).write_text(json.dumps(index, sort_keys=True, indent=2) + "\n")
    for key, value in metadata[ordered[0]].items():
        if key not in {"release_dir", "asset_count"}:
            print(key + "=" + value)
    print("release_dir=" + str(output))
    print("targets=" + ",".join(ordered))
    print("asset_count=" + str(len(list(output.iterdir()))))
    return 0
