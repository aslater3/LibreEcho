#!/usr/bin/env python3
"""Plan changed-only reboot-bound OTA v2 feature assets for Product builds."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ota_v2_product import (  # noqa: E402
    COMMIT40,
    DAEMONS,
    FEATURES,
    HEX64,
    ContractError,
    digest,
    fail,
    load_json,
    verify_runtime_capsule,
    validate_file_records,
    validate_inventory,
    validate_plan,
)


def strict_catalog(path: Path) -> dict[str, dict[str, Any]]:
    value = load_json(path, "catalog")
    features = value.get("features") if isinstance(value, dict) else None
    if not isinstance(features, dict) or set(features) != set(FEATURES):
        fail("catalog must contain exactly the five features")
    result: dict[str, dict[str, Any]] = {}
    for feature in FEATURES:
        record = features[feature]
        if not isinstance(record, dict) or set(record) != {"payload", "manifest"}:
            fail(f"catalog record is malformed: {feature}")
        checked: dict[str, Any] = {}
        for kind in ("payload", "manifest"):
            artifact = record[kind]
            if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256", "size"}:
                fail(f"catalog {kind} record is malformed: {feature}")
            if not isinstance(artifact["path"], str) or not artifact["path"]:
                fail(f"catalog {kind} path is malformed: {feature}")
            if (not isinstance(artifact["sha256"], str)
                    or HEX64.fullmatch(artifact["sha256"]) is None):
                fail(f"catalog {kind} hash is malformed: {feature}")
            if (not isinstance(artifact["size"], int) or isinstance(artifact["size"], bool)
                    or not 0 <= artifact["size"] < (1 << 63)):
                fail(f"catalog {kind} size is malformed: {feature}")
            source = Path(artifact["path"])
            actual_hash, actual_size = digest(source)
            if artifact["sha256"] != actual_hash or artifact["size"] != actual_size:
                fail(f"catalog {kind} identity mismatch: {feature}")
            checked[kind] = {"path": source, "sha256": actual_hash, "size": actual_size}
        feature_manifest = load_json(checked["manifest"]["path"], f"{feature} feature manifest")
        if (not isinstance(feature_manifest, dict) or feature_manifest.get("schema_version") != 1
                or feature_manifest.get("feature_id") != feature
                or feature_manifest.get("format") != "squashfs-lz4"):
            fail(f"{feature} feature manifest is incompatible")
        payload = feature_manifest.get("payload")
        files = feature_manifest.get("files")
        if not isinstance(payload, dict):
            fail(f"{feature} feature manifest lacks payload/files")
        files = validate_file_records(files, f"{feature} feature manifest")
        if (payload.get("filename") != checked["payload"]["path"].name
                or payload.get("sha256") != checked["payload"]["sha256"]
                or payload.get("size") != checked["payload"]["size"]):
            fail(f"{feature} feature manifest does not match payload")
        daemon = files.get(DAEMONS[feature])
        if not isinstance(daemon, dict) or not isinstance(daemon.get("sha256"), str):
            fail(f"{feature} daemon identity is unavailable")
        checked["files"] = files
        result[feature] = checked
    return result


def runtime_record(path: Path, feature: str, base: dict[str, Any], candidate: dict[str, Any], release: str, source_commit: str, verifier: Path | None = None) -> dict[str, Any]:
    payload_path = path / f"{feature}.runtime.squashfs"
    manifest_path = path / f"{feature}.runtime-manifest.json"
    payload_hash, payload_size = digest(payload_path)
    runtime = load_json(manifest_path, f"{feature} runtime capsule manifest")
    if (not isinstance(runtime, dict) or runtime.get("schema_version") != 1
            or runtime.get("kind") != "runtime-capsule" or runtime.get("feature_id") != feature
            or runtime.get("format") != "squashfs-lz4"):
        fail(f"runtime capsule manifest is incompatible: {feature}")
    if (runtime.get("base_payload_sha256") != base["payload"]["sha256"]
            or runtime.get("base_manifest_sha256") != base["manifest"]["sha256"]):
        fail(f"runtime capsule base identity is incompatible: {feature}")
    if verifier is not None:
        verify_runtime_capsule(verifier, feature, base, path, release, source_commit)
    payload = runtime.get("payload")
    files = runtime.get("files")
    if (not isinstance(payload, dict) or payload.get("filename") != payload_path.name
            or payload.get("sha256") != payload_hash or payload.get("size") != payload_size):
        fail(f"runtime capsule payload identity is incompatible: {feature}")
    files = validate_file_records(files, f"{feature} runtime capsule")
    daemon_path = DAEMONS[feature]
    daemon = files.get(daemon_path)
    candidate_daemon = candidate["files"].get(daemon_path)
    if (not isinstance(daemon, dict) or not isinstance(candidate_daemon, dict)
            or daemon.get("sha256") != candidate_daemon.get("sha256")):
        fail(f"runtime capsule daemon identity is incompatible: {feature}")
    # A capsule may only cover the changed regular files.  This prevents an
    # arbitrary capsule from being selected merely because the pair exists.
    base_files, candidate_files = base["files"], candidate["files"]
    changed = {name for name in set(base_files) | set(candidate_files)
               if base_files.get(name, {}).get("sha256") != candidate_files.get(name, {}).get("sha256")}
    covered = set(files)
    if changed - covered or covered - changed:
        fail(f"runtime capsule file allowlist is incompatible: {feature}")
    for name in changed:
        candidate_file = candidate_files.get(name)
        runtime_file = files.get(name)
        if (not isinstance(candidate_file, dict) or not isinstance(runtime_file, dict)
                or runtime_file.get("sha256") != candidate_file.get("sha256")):
            fail(f"runtime capsule file identity is incompatible: {feature}")
    prefix = f"libreecho-radar-puffin-{release}-{feature}"
    return {
        "feature_id": feature, "action": "runtime", "activation": "reboot",
        "base_payload_sha256": base["payload"]["sha256"],
        "base_manifest_sha256": base["manifest"]["sha256"], "daemon_path": daemon_path,
        "daemon_sha256": candidate_daemon["sha256"], "release": release,
        "source_commit": source_commit, "asset": prefix + ".runtime.squashfs",
        "size": payload_size, "sha256": payload_hash,
        "manifest_asset": prefix + ".runtime-manifest.json",
        "manifest_size": manifest_path.stat().st_size, "manifest_sha256": digest(manifest_path)[0],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-channel", choices=("dev", "stable"), default="stable")
    parser.add_argument("--base-catalog", type=Path, required=True)
    parser.add_argument("--candidate-catalog", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--release", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory-output", type=Path)
    parser.add_argument("--asset-output-dir", type=Path)
    parser.add_argument("--platform-runtime-verifier", type=Path)
    args = parser.parse_args()
    try:
        if not __import__("re").fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.release):
            fail("release must be numeric SemVer")
        if COMMIT40.fullmatch(args.source_commit) is None:
            fail("source commit is malformed")
        from device_baseline import SCHEMA as DEVICE_SCHEMA, parse as parse_device, validate_plan as validate_device_plan
        candidate = strict_catalog(args.candidate_catalog)
        raw_base = load_json(args.base_catalog, "base catalog")
        device = None
        if isinstance(raw_base, dict) and raw_base.get("schema") == DEVICE_SCHEMA:
            device = parse_device(args.base_catalog.read_text(), args.update_channel)
            if args.runtime_dir:
                fail("device migration forbids runtime capsules")
            base = {fid: {
                "payload": {"sha256": r["payload_sha256"]},
                "manifest": {"sha256": r["manifest_sha256"]},
                "files": {DAEMONS[fid]: {"sha256": r["daemon_sha256"]}},
            } for fid, r in device["features"].items()}
            # Keep the observed wakeword unless the hash-bound dev input opts in.
            if device.get("replace_wakeword") is not True:
                candidate["wakeword"] = base["wakeword"]
        else:
            base = strict_catalog(args.base_catalog)
        if args.runtime_dir and args.runtime_dir.is_symlink():
            fail("runtime directory must not be a symlink")
        records: list[dict[str, Any]] = []
        inventory: list[dict[str, Any]] = []
        for feature in FEATURES:
            old, new = base[feature], candidate[feature]
            old_daemon = old["files"][DAEMONS[feature]]["sha256"]
            new_daemon = new["files"][DAEMONS[feature]]["sha256"]
            common = {
                "feature_id": feature, "activation": "reboot",
                "base_payload_sha256": old["payload"]["sha256"],
                "base_manifest_sha256": old["manifest"]["sha256"],
                "daemon_path": DAEMONS[feature], "daemon_sha256": new_daemon,
                "release": args.release, "source_commit": args.source_commit,
            }
            changed = (old["payload"]["sha256"], old["manifest"]["sha256"], old_daemon) != (
                new["payload"]["sha256"], new["manifest"]["sha256"], new_daemon)
            if not changed:
                record = {**common, "action": "preserve"}
            elif args.runtime_dir and args.runtime_dir.is_dir():
                pair = (args.runtime_dir / f"{feature}.runtime.squashfs", args.runtime_dir / f"{feature}.runtime-manifest.json")
                if pair[0].exists() or pair[1].exists():
                    record = runtime_record(args.runtime_dir, feature, old, new, args.release, args.source_commit, args.platform_runtime_verifier)
                else:
                    record = {**common, "action": "replace"}
                    record.update({"asset": f"libreecho-radar-puffin-{args.release}-{feature}.payload.squashfs", "size": new["payload"]["size"], "sha256": new["payload"]["sha256"], "manifest_asset": f"libreecho-radar-puffin-{args.release}-{feature}.manifest.json", "manifest_size": new["manifest"]["size"], "manifest_sha256": new["manifest"]["sha256"]})
            else:
                record = {**common, "action": "replace"}
                record.update({"asset": f"libreecho-radar-puffin-{args.release}-{feature}.payload.squashfs", "size": new["payload"]["size"], "sha256": new["payload"]["sha256"], "manifest_asset": f"libreecho-radar-puffin-{args.release}-{feature}.manifest.json", "manifest_size": new["manifest"]["size"], "manifest_sha256": new["manifest"]["sha256"]})
            records.append(record)
            if record["action"] != "preserve":
                inventory.extend([
                    {"feature_id": feature, "action": record["action"], "kind": "payload", "name": record["asset"], "size": record["size"], "sha256": record["sha256"]},
                    {"feature_id": feature, "action": record["action"], "kind": "manifest", "name": record["manifest_asset"], "size": record["manifest_size"], "sha256": record["manifest_sha256"]},
                ])
        plan = {"schema": "libreecho-product-feature-plan-v1", "transaction_type": "system", "activation": "reboot", "release": args.release, "source_commit": args.source_commit, "features": records}
        validate_plan(plan, args.release, args.source_commit)
        if device is not None:
            validate_device_plan(device, plan)
        if args.asset_output_dir:
            if args.asset_output_dir.exists():
                fail("OTA asset output directory already exists")
            args.asset_output_dir.mkdir(parents=True)
            sources: dict[str, Path] = {}
            for feature in FEATURES:
                if not any(item["feature_id"] == feature for item in inventory):
                    continue
                record = next(item for item in records if item["feature_id"] == feature)
                source_dir = args.runtime_dir if record["action"] == "runtime" else Path(candidate[feature]["payload"]["path"]).parent
                sources[record["asset"]] = source_dir / (f"{feature}.runtime.squashfs" if record["action"] == "runtime" else Path(candidate[feature]["payload"]["path"]).name)
                sources[record["manifest_asset"]] = source_dir / (f"{feature}.runtime-manifest.json" if record["action"] == "runtime" else Path(candidate[feature]["manifest"]["path"]).name)
            for item in inventory:
                source = sources[item["name"]]
                actual_hash, actual_size = digest(source)
                if (actual_hash, actual_size) != (item["sha256"], item["size"]):
                    fail(f"asset identity mismatch: {item['name']}")
                shutil.copyfile(source, args.asset_output_dir / item["name"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(plan, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        inventory_data = {"schema": "libreecho-product-feature-assets-v1", "transaction_type": "system", "activation": "reboot", "release": args.release, "source_commit": args.source_commit, "assets": sorted(inventory, key=lambda item: item["name"])}
        validate_inventory(inventory_data, records, args.release)
        inventory_path = args.inventory_output or args.output.with_name("feature-assets.json")
        inventory_path.parent.mkdir(parents=True, exist_ok=True)
        inventory_path.write_text(json.dumps(inventory_data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        print(f"feature_plan={args.output}")
        print(f"feature_asset_inventory={inventory_path}")
        print(f"changed_features={sum(1 for r in records if r['action'] != 'preserve')}")
        return 0
    except (ContractError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
