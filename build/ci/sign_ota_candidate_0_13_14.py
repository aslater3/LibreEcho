#!/usr/bin/env python3
"""Protected, host-verifiable signer for a Product OTA v2 candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ota_v2_product import (
    CANONICAL_PARSER_RELATIVE, CANONICAL_PARSER_SCHEMA, digest,
    expected_public_key_sha256, load_feature_contract,
)

SCHEMA = "libreecho-ota-v2-signing-handoff-v1"
EXPECTED_RELEASE = "0.13.14"
FEATURES = ("airplay2", "tts", "wakeword", "stt", "assistant")
HEX64 = set("0123456789abcdef")


class HandoffError(ValueError):
    """Raised when the immutable signing handoff is incomplete or altered."""


def _record(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise HandoffError(f"handoff asset is not a regular file: {path.name}")
    sha256, size = digest(path)
    return {"name": path.name, "path": str(path), "size": size, "sha256": sha256}


def _check_record(record: Any, label: str) -> Path:
    if not isinstance(record, dict) or set(record) != {"name", "path", "size", "sha256"}:
        raise HandoffError(f"{label} record is malformed")
    if not isinstance(record["name"], str) or not isinstance(record["path"], str):
        raise HandoffError(f"{label} path identity is malformed")
    path = Path(record["path"])
    if path.name != record["name"]:
        raise HandoffError(f"{label} path identity mismatch")
    if not isinstance(record["size"], int) or isinstance(record["size"], bool) or record["size"] < 1:
        raise HandoffError(f"{label} size is malformed")
    if not isinstance(record["sha256"], str) or len(record["sha256"]) != 64 or any(c not in "0123456789abcdef" for c in record["sha256"]):
        raise HandoffError(f"{label} hash is malformed")
    actual = _record(path)
    if (actual["size"], actual["sha256"]) != (record["size"], record["sha256"]):
        raise HandoffError(f"{label} hash mismatch")
    return path


def _git_state(source: Path) -> tuple[str, str]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=True, timeout=30,
        ).stdout.strip()
        diff = subprocess.run(
            ["git", "-C", str(source), "diff", "--binary", "--full-index", "HEAD"],
            capture_output=True, check=True, timeout=30,
        ).stdout
        untracked = subprocess.run(
            ["git", "-C", str(source), "ls-files", "--others", "--exclude-standard", "-z"],
            capture_output=True, check=True, timeout=30,
        ).stdout.split(b"\0")
        state = bytearray(diff)
        for relative in sorted(item for item in untracked if item):
            candidate = source / os.fsdecode(relative)
            if candidate.is_symlink() or not candidate.is_file():
                raise HandoffError("Platform source contains an unsafe untracked entry")
            state.extend(b"\0untracked:")
            state.extend(relative)
            state.extend(b"\0")
            state.extend(hashlib.sha256(candidate.read_bytes()).hexdigest().encode("ascii"))
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise HandoffError(f"Platform source identity could not be read: {exc}") from exc
    if len(commit) != 40 or any(char not in HEX64 for char in commit):
        raise HandoffError("Platform source commit is malformed")
    return commit, hashlib.sha256(bytes(state)).hexdigest()


def _dependency_tree_files(root: Path) -> list[dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise HandoffError(f"dependency root is unavailable or unsafe: {root}")
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        # Bytecode caches are the only intentionally excluded paths.  Native
        # extensions, executable files, metadata, and all other regular files
        # remain in the measured closure.
        if "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            raise HandoffError(f"dependency closure contains a symlink: {path}")
        if path.is_file():
            record = _record(path)
            record["relative_path"] = str(relative)
            record["mode"] = path.stat().st_mode & 0o7777
            files.append(record)
        elif not path.is_dir():
            raise HandoffError(f"dependency closure contains an unsafe entry: {path}")
    return files


def _dependency_tree_hash(root: Path) -> str:
    files = _dependency_tree_files(root)
    material = [
        {key: item[key] for key in ("relative_path", "size", "sha256", "mode")}
        for item in files
    ]
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _distribution_identity(name: str) -> dict[str, Any]:
    try:
        distribution = importlib.metadata.distribution(name)
        version = distribution.version
        paths = distribution.files
    except importlib.metadata.PackageNotFoundError as exc:
        raise HandoffError(f"required signing package is unavailable: {name}") from exc
    if paths is None:
        raise HandoffError(f"signing package has no measured file list: {name}")
    files: list[dict[str, Any]] = []
    for relative in sorted(paths, key=str):
        path = Path(distribution.locate_file(relative))
        if path.is_symlink() or not path.is_file():
            raise HandoffError(f"signing package file is unavailable or unsafe: {path}")
        record = _record(path)
        record["relative_path"] = str(relative)
        record["mode"] = path.stat().st_mode & 0o7777
        files.append(record)
    if not files:
        raise HandoffError(f"signing package has no measured files: {name}")
    return {
        "name": name, "version": version,
        "path": str(Path(distribution.locate_file(""))),
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "files": files,
    }


def _product_runtime_identity() -> dict[str, Any]:
    ci_root = Path(__file__).resolve().parent
    product_root = ci_root.parents[1]
    product_files = [
        {key: record[key] for key in ("name", "path", "size", "sha256", "mode")}
        for record in _dependency_tree_files(ci_root)
    ]
    orchestrator = product_root / "build" / "build.sh"
    if orchestrator.is_symlink() or not orchestrator.is_file():
        raise HandoffError("Product build orchestrator is unavailable or unsafe")
    orchestrator_record = _record(orchestrator)
    orchestrator_record["mode"] = orchestrator.stat().st_mode & 0o7777
    product_files.append(orchestrator_record)
    product_files.sort(key=lambda item: item["path"])
    return {
        "interpreter": _record(Path(sys.executable).resolve()),
        "product_tools": product_files,
        "packages": [_distribution_identity(name) for name in ("PyNaCl", "cffi", "pycparser")],
        "excluded": ["**/__pycache__/**"],
    }


def _check_measured_file(record: Any, label: str) -> None:
    if (not isinstance(record, dict)
            or set(record) != {"name", "path", "size", "sha256"}):
        raise HandoffError(f"{label} record is malformed")
    path = Path(record["path"])
    if _record(path) != record:
        raise HandoffError(f"{label} file identity changed")


def _check_product_runtime_binding(binding: Any) -> None:
    if (not isinstance(binding, dict)
            or set(binding) != {"interpreter", "product_tools", "packages", "excluded"}
            or binding.get("excluded") != ["**/__pycache__/**"]
            or not isinstance(binding.get("product_tools"), list)
            or not isinstance(binding.get("packages"), list)):
        raise HandoffError("Product signing runtime identity is incomplete")
    _check_measured_file(binding["interpreter"], "Product signing interpreter")
    tools = binding["product_tools"]
    if not tools:
        raise HandoffError("Product signing runtime identity is incomplete")
    paths: set[str] = set()
    for record in tools:
        if not isinstance(record, dict) or set(record) != {"name", "path", "size", "sha256", "mode"}:
            raise HandoffError("Product signing runtime identity is malformed")
        path = Path(record["path"])
        if str(path) in paths or path.is_symlink() or not path.is_file():
            raise HandoffError("Product signing runtime identity is malformed")
        paths.add(str(path))
        actual = _record(path)
        if any(record[key] != actual[key] for key in ("name", "path", "size", "sha256")) or path.stat().st_mode & 0o7777 != record["mode"]:
            raise HandoffError("Product signing runtime identity changed")
    packages = binding["packages"]
    if len({item.get("name") for item in packages if isinstance(item, dict)}) != len(packages):
        raise HandoffError("Product signing package identity is malformed")
    for item in packages:
        if not isinstance(item, dict) or set(item) != {"name", "version", "path", "sha256", "files"}:
            raise HandoffError("Product signing package identity is malformed")
        actual = _distribution_identity(str(item["name"]))
        if actual != item:
            raise HandoffError("Product signing runtime identity changed")


def _platform_identity(platform_source: Path, platform_tool: Path) -> dict[str, Any]:
    if platform_source.is_symlink() or not platform_source.is_dir():
        raise HandoffError("Platform source is unavailable or unsafe")
    tool = platform_tool.absolute()
    source = platform_source.absolute()
    try:
        tool.relative_to(source)
    except ValueError as exc:
        raise HandoffError("Platform tool is outside the bound Platform source") from exc
    tool_record = _record(tool)
    dependency_paths = [
        tool.parent / "feature_manifest.py",
        tool.parent.parent / "feature_runtime" / "verify_runtime.py",
    ]
    dependency_records = [_record(path) for path in dependency_paths]
    try:
        nacl_path = Path(importlib.metadata.distribution("PyNaCl").locate_file("nacl"))
        nacl_version = importlib.metadata.version("PyNaCl")
    except importlib.metadata.PackageNotFoundError as exc:
        raise HandoffError("Platform signing dependency PyNaCl is unavailable") from exc
    if nacl_path.is_symlink() or not nacl_path.is_dir():
        raise HandoffError("Platform signing dependency path is unsafe")
    dependency_records.append({
        "name": "PyNaCl",
        "path": str(nacl_path),
        "version": nacl_version,
        "sha256": _dependency_tree_hash(nacl_path),
        "files": _dependency_tree_files(nacl_path),
        "excluded": ["**/__pycache__/**"],
    })
    commit, diff_sha256 = _git_state(source)
    parser = tool.parent / "feature_manifest.py"
    return {
        "path": str(source), "commit": commit, "diff_sha256": diff_sha256,
        "tool": tool_record, "dependencies": dependency_records,
        "canonical_parser": {
            "schema": CANONICAL_PARSER_SCHEMA,
            "relative_path": CANONICAL_PARSER_RELATIVE,
            "record": _record(parser),
        },
    }


def _check_platform_binding(data: dict[str, Any], supplied_tool: Path) -> None:
    binding = data.get("platform_source")
    tool_record = data.get("platform_tool")
    dependencies = data.get("platform_dependencies")
    parser_binding = data.get("canonical_parser")
    if (not isinstance(binding, dict) or set(binding) != {"path", "commit", "diff_sha256"}
            or not isinstance(tool_record, dict) or not isinstance(dependencies, list)
            or not isinstance(tool_record.get("path"), str)
            or set(tool_record) != {"name", "path", "size", "sha256"}
            or len(dependencies) != 3
            or not all(isinstance(binding.get(key), str) for key in ("path", "commit", "diff_sha256"))):
        raise HandoffError("signing handoff Platform identity binding is incomplete")
    if (not all(isinstance(item, dict) for item in dependencies)
            or sum(item.get("name") == "PyNaCl" for item in dependencies) != 1):
        raise HandoffError("signing handoff Platform dependency identity is incomplete")
    source = Path(binding["path"])
    commit, diff_sha256 = _git_state(source)
    if commit != binding["commit"] or diff_sha256 != binding["diff_sha256"]:
        raise HandoffError("Platform source identity changed")
    actual = _record(supplied_tool)
    if actual != tool_record:
        raise HandoffError("Platform signer tool identity changed or was substituted")
    expected = [item for item in dependencies if isinstance(item, dict) and item.get("name") != "PyNaCl"]
    if len(expected) != 2:
        raise HandoffError("signing handoff Platform dependency identity is incomplete")
    for item in expected:
        if (set(item) != {"name", "path", "size", "sha256"}
                or not all(isinstance(item.get(key), str) for key in ("name", "path", "sha256"))
                or not isinstance(item.get("size"), int)
                or _record(Path(item["path"])) != item):
            raise HandoffError("Platform signer dependency identity changed")
    if parser_binding is not None:
        if (not isinstance(parser_binding, dict)
                or set(parser_binding) != {"schema", "relative_path", "record"}
                or parser_binding.get("schema") != CANONICAL_PARSER_SCHEMA
                or parser_binding.get("relative_path") != CANONICAL_PARSER_RELATIVE):
            raise HandoffError("canonical v2 parser provenance is malformed")
        record = parser_binding.get("record")
        if not isinstance(record, dict) or _record(Path(record.get("path", ""))) != record:
            raise HandoffError("canonical v2 parser provenance changed")
        try:
            Path(record["path"]).absolute().relative_to(source.absolute())
        except (KeyError, TypeError, ValueError) as exc:
            raise HandoffError("canonical v2 parser is outside the bound source") from exc
    try:
        supplied_tool.absolute().relative_to(source.absolute())
        for item in expected:
            Path(item["path"]).absolute().relative_to(source.absolute())
    except (TypeError, ValueError) as exc:
        raise HandoffError("Platform signer dependency is outside the bound source") from exc
    try:
        version = importlib.metadata.version("PyNaCl")
        nacl_path = Path(importlib.metadata.distribution("PyNaCl").locate_file("nacl"))
    except importlib.metadata.PackageNotFoundError as exc:
        raise HandoffError("Platform signing dependency PyNaCl is unavailable") from exc
    nacl_item = next((item for item in dependencies if isinstance(item, dict) and item.get("name") == "PyNaCl"), None)
    if (not isinstance(nacl_item, dict) or nacl_item.get("version") != version
            or nacl_item.get("path") != str(nacl_path)
            or nacl_item.get("sha256") != _dependency_tree_hash(nacl_path)
            or nacl_item.get("excluded") != ["**/__pycache__/**"]
            or nacl_item.get("files") != _dependency_tree_files(nacl_path)):
        raise HandoffError("Platform signing dependency identity changed")


def validate_handoff(path: Path, expected_release: str = EXPECTED_RELEASE) -> dict[str, Any]:
    if expected_release != EXPECTED_RELEASE:
        raise HandoffError(f"signing handoff release must be {EXPECTED_RELEASE}")
    if path.is_symlink() or not path.is_file():
        raise HandoffError("signing handoff is unavailable")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HandoffError(f"signing handoff is malformed: {exc}") from exc
    required = {"schema", "format", "release", "source_commit", "update_channel", "base_catalog_sha256", "run_dir", "base_catalog", "build_manifest", "boot_image", "feature_plan", "feature_asset_inventory", "assets", "platform_source", "platform_tool", "platform_dependencies", "canonical_parser", "product_runtime"}
    if not isinstance(data, dict) or set(data) != required or data.get("schema") != SCHEMA:
        raise HandoffError("signing handoff schema mismatch")
    if data["format"] != "v2" or data["release"] != expected_release:
        raise HandoffError("signing handoff release or format mismatch")
    if data["update_channel"] not in {"dev", "stable"}:
        raise HandoffError("signing handoff channel is invalid")
    if not isinstance(data["source_commit"], str) or len(data["source_commit"]) != 40 or any(c not in "0123456789abcdef" for c in data["source_commit"]):
        raise HandoffError("signing handoff source commit is malformed")
    if not isinstance(data["base_catalog_sha256"], str) or len(data["base_catalog_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in data["base_catalog_sha256"]):
        raise HandoffError("signing handoff base catalog hash is malformed")
    base_catalog = _check_record(data["base_catalog"], "base catalog")
    from device_baseline import SCHEMA as DEVICE_SCHEMA, parse as parse_device, validate_plan as validate_device_plan
    baseline_data = json.loads(base_catalog.read_text())
    if isinstance(baseline_data, dict) and baseline_data.get("schema") == DEVICE_SCHEMA:
        device = parse_device(base_catalog.read_text(), data["update_channel"])
        plan_path = _check_record(data["feature_plan"], "feature plan")
        validate_device_plan(device, json.loads(plan_path.read_text()))
    if data["base_catalog"]["sha256"] != data["base_catalog_sha256"]:
        raise HandoffError("signing handoff base catalog identity mismatch")
    if not isinstance(data["run_dir"], str):
        raise HandoffError("signing handoff run directory is malformed")
    run = Path(data["run_dir"])
    if run.is_symlink() or not run.is_dir():
        raise HandoffError("signing handoff run directory is unavailable")
    for key in ("base_catalog", "build_manifest", "boot_image", "feature_plan", "feature_asset_inventory"):
        record_path = _check_record(data[key], key)
        if record_path.parent != run:
            raise HandoffError(f"{key} is outside the candidate run")
    assets = data["assets"]
    if not isinstance(assets, list) or not assets:
        raise HandoffError("signing handoff asset list is empty")
    names: set[str] = set()
    for item in assets:
        asset = _check_record(item, "candidate asset")
        if asset.name in names:
            raise HandoffError("signing handoff contains duplicate asset")
        names.add(asset.name)
    platform_tool = data.get("platform_tool")
    if not isinstance(platform_tool, dict) or not isinstance(platform_tool.get("path"), str):
        raise HandoffError("signing handoff Platform tool identity is malformed")
    _check_platform_binding(data, Path(platform_tool["path"]))
    _check_product_runtime_binding(data["product_runtime"])
    if {item.get("name") for item in data["product_runtime"]["packages"]} != {"PyNaCl", "cffi", "pycparser"}:
        raise HandoffError("Product signing package closure is incomplete")
    return data


def _trusted_key_digest(public_key: Path, supplied: str | None = None) -> str:
    trusted = os.environ.get("LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256", "")
    if not trusted:
        raise HandoffError("trusted OTA public-key digest anchor is missing")
    if supplied is not None and supplied != trusted:
        raise HandoffError("supplied OTA public-key digest is not the trusted workflow anchor")
    try:
        return expected_public_key_sha256(public_key, trusted)
    except ValueError as exc:
        raise HandoffError(str(exc)) from exc


def create_handoff(run: Path, release: str, source_commit: str, update_channel: str, base_catalog: Path, base_catalog_sha256: str, output: Path, platform_source: Path, platform_tool: Path) -> None:
    if release != EXPECTED_RELEASE:
        raise HandoffError(f"signing handoff release must be {EXPECTED_RELEASE}")
    if not base_catalog.is_file() or base_catalog.is_symlink() or digest(base_catalog)[0] != base_catalog_sha256:
        raise HandoffError("base catalog hash mismatch")
    plan = run / "feature-plan.json"
    inventory = run / "feature-assets.json"
    asset_dir = run / "ota-assets"
    build_manifest = run / "manifest.json"
    boot = run / "boot.img"
    _trusted_key_digest(run / "ota-public-key.hex")
    _, _, _, inventory_assets = load_feature_contract(run, {
        "ota_format": "v2", "ota_release": release, "ui_commit": source_commit,
        "feature_plan": str(plan), "feature_asset_inventory": str(inventory),
        "feature_asset_dir": str(asset_dir),
    })
    assets = [_record(boot), _record(build_manifest)]
    for feature in FEATURES:
        assets.extend((_record(run / "features" / f"{feature}.squashfs"), _record(run / "features" / f"{feature}.manifest.json")))
    for item in inventory_assets:
        assets.append(_record(asset_dir / item["name"]))
    data = {
        "schema": SCHEMA, "format": "v2", "release": release,
        "source_commit": source_commit, "update_channel": update_channel,
        "base_catalog_sha256": base_catalog_sha256, "run_dir": str(run),
        "base_catalog": _record(base_catalog),
        "build_manifest": _record(build_manifest), "boot_image": _record(boot),
        "feature_plan": _record(plan), "feature_asset_inventory": _record(inventory),
        "assets": assets,
    }
    identity = _platform_identity(platform_source, platform_tool)
    data["platform_source"] = {
        "path": identity["path"], "commit": identity["commit"],
        "diff_sha256": identity["diff_sha256"],
    }
    data["platform_tool"] = identity["tool"]
    data["platform_dependencies"] = identity["dependencies"]
    data["canonical_parser"] = identity["canonical_parser"]
    data["product_runtime"] = _product_runtime_identity()
    if output.exists() or output.is_symlink():
        raise HandoffError("refusing to overwrite signing handoff")
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validate_handoff(output)


def invoke_platform_signer(
    platform_tool: Path, *, boot_image: Path, build_manifest: Path,
    signing_key: Path, public_key: Path, output: Path, plan: Path,
    release: str, update_channel: str,
) -> list[str]:
    command = [
        sys.executable, str(platform_tool), "--format", "v2",
        "--boot-image", str(boot_image), "--build-manifest", str(build_manifest),
        "--version", release, "--signing-key", str(signing_key),
        "--public-key", str(public_key), "--service-profile", "production",
        "--feature-policy", "community-noncommercial", "--update-channel", update_channel,
        "--feature-plan", str(plan), "--output", str(output),
    ]
    subprocess.run(command, check=True)
    return command


def sign(handoff_path: Path, platform_tool: Path, signing_key: Path, public_key: Path, output: Path, expected_key_sha256: str | None = None) -> None:
    data = validate_handoff(handoff_path)
    _trusted_key_digest(public_key, expected_key_sha256)
    run = Path(data["run_dir"])
    feature_plan, feature_inventory, feature_asset_dir, inventory_assets = load_feature_contract(run, {
        "ota_format": "v2", "ota_release": data["release"], "ui_commit": data["source_commit"],
        "feature_plan": data["feature_plan"]["path"],
        "feature_asset_inventory": data["feature_asset_inventory"]["path"],
        "feature_asset_dir": str(run / "ota-assets"),
    })
    expected_assets = {
        "boot.img", "manifest.json",
        *[f"{feature}.{suffix}" for feature in FEATURES for suffix in ("squashfs", "manifest.json")],
        *[str(item["name"]) for item in inventory_assets],
    }
    actual_assets = {str(item["name"]) for item in data["assets"]}
    if actual_assets != expected_assets:
        raise HandoffError("signing handoff candidate asset inventory mismatch")
    if output.exists() or output.is_symlink():
        raise HandoffError("refusing to overwrite signed OTA output")
    _check_platform_binding(data, platform_tool)
    invoke_platform_signer(
        platform_tool, boot_image=Path(data["boot_image"]["path"]),
        build_manifest=Path(data["build_manifest"]["path"]), signing_key=signing_key,
        public_key=public_key, output=output, plan=Path(data["feature_plan"]["path"]),
        release=data["release"], update_channel=data["update_channel"],
    )
    if not output.is_file() or output.is_symlink():
        raise HandoffError("Platform signer did not produce a regular OTA bundle")
    from ota_v2_product import validate_control_tar
    validate_control_tar(
        output, public_key, "v2", data["release"],
        feature_plan=feature_plan,
        feature_inventory=feature_inventory,
        feature_asset_dir=feature_asset_dir,
        expected_channel=data["update_channel"],
        boot_path=Path(data["boot_image"]["path"]),
        expected_key_sha256=os.environ["LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256"],
    )
    print(f"ota_bundle={output}")
    print(f"ota_bundle_sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")
    print(f"feature_plan={data['feature_plan']['path']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    create = sub.add_parser("create-handoff")
    create.add_argument("--run-dir", type=Path, required=True)
    create.add_argument("--release", required=True)
    create.add_argument("--source-commit", required=True)
    create.add_argument("--update-channel", required=True)
    create.add_argument("--base-catalog", type=Path, required=True)
    create.add_argument("--base-catalog-sha256", required=True)
    create.add_argument("--platform-source", type=Path, required=True)
    create.add_argument("--platform-tool", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    sign_parser = sub.add_parser("sign")
    sign_parser.add_argument("--handoff", type=Path, required=True)
    sign_parser.add_argument("--platform-tool", type=Path, required=True)
    sign_parser.add_argument("--signing-key", type=Path, required=True)
    sign_parser.add_argument("--public-key", type=Path, required=True)
    sign_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == "create-handoff":
            create_handoff(args.run_dir, args.release, args.source_commit, args.update_channel, args.base_catalog, args.base_catalog_sha256, args.output, args.platform_source, args.platform_tool)
        else:
            sign(args.handoff, args.platform_tool, args.signing_key, args.public_key, args.output)
    except (HandoffError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
