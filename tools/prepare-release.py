#!/usr/bin/env python3
"""Create sanitized public LibreEcho release provenance from a private candidate.

The candidate is read-only input. No path, run ID, device identity, credential,
firmware byte, or operational deployment field is copied to public output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

RELEASE_ID = re.compile(r"^radar-puffin-v[0-9]+\.[0-9]+\.[0-9]+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
EMPTY_DIFF_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
REPO = re.compile(r"^https://github\.com/[^/]+/[^/]+$")
RUN_ID = re.compile(r"(?:^|[-_])20\d{6}T\d{6}Z(?:[-_]|$)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source(repository: str, commit: str) -> dict[str, str]:
    if not REPO.fullmatch(repository) or not COMMIT.fullmatch(commit):
        raise SystemExit("ERROR: source repository or commit is not a public immutable identity")
    return {"repository": repository, "commit": commit}


def load_candidate(path: Path) -> tuple[dict[str, str], dict[str, object]]:
    if path.suffix == ".json":
        record = json.loads(path.read_text())
    else:
        record = {}
        for line in path.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                record[key] = value
    manifest_path = Path(str(record.get("manifest", path.parent / "manifest.json")))
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise SystemExit("ERROR: candidate manifest is unavailable")
    return record, json.loads(manifest_path.read_text())


RELEASE_SCOPES = {"commercially-unrestricted", "community-noncommercial"}


def load_components(
    path: Path, release_scope: str = "commercially-unrestricted"
) -> list[dict[str, object]]:
    if release_scope not in RELEASE_SCOPES:
        raise SystemExit(f"ERROR: unknown release scope: {release_scope}")
    if not path.is_file() or path.is_symlink():
        raise SystemExit("ERROR: public component allowlist is unavailable")
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or data.get("schema_version") != 2 or
            not isinstance(data.get("components"), list)):
        raise SystemExit("ERROR: public component allowlist is malformed")
    redistributed_scopes = {"source-release", "core-image", "separate-payload"}
    local_scopes = {"local-extraction-only", "external-user-supplied"}
    documented_good_faith_status = "documented-good-faith"
    required_good_faith_fields = {
        "provenance_note", "source_origin", "known_good_sha256",
        "known_good_size", "redistribution_basis", "license_finding",
    }
    failures = []
    seen = set()
    applicable = []
    for component in data["components"]:
        if not isinstance(component, dict):
            failures.append("<malformed>: record is not an object")
            continue
        component_id = component.get("id")
        required = {
            "id", "name", "version", "license", "release_status",
            "distribution_scope", "download_location", "source_offer", "evidence",
        }
        if (not isinstance(component_id, str) or not component_id or
                component_id in seen or not required.issubset(component)):
            failures.append(f"{component_id or '<unknown>'}: malformed or duplicate")
            continue
        seen.add(component_id)
        scope = component.get("distribution_scope")
        status = component.get("release_status")
        allowed_scopes = component.get("allowed_release_scopes")
        if allowed_scopes is None:
            component_release_scopes = RELEASE_SCOPES
        elif (not isinstance(allowed_scopes, list) or not allowed_scopes or
              len(allowed_scopes) != len(set(allowed_scopes)) or
              not all(isinstance(item, str) and item in RELEASE_SCOPES
                      for item in allowed_scopes)):
            failures.append(f"{component_id}: invalid allowed release scopes")
            component_release_scopes = set()
        else:
            component_release_scopes = set(allowed_scopes)
        noncommercial = (
            "CC-BY-NC-" in str(component.get("license", "")) or
            component.get("use_restriction") == "noncommercial-model-asset"
        )
        if noncommercial:
            if component.get("use_restriction") != "noncommercial-model-asset":
                failures.append(f"{component_id}: noncommercial use restriction is missing")
            if component_release_scopes != {"community-noncommercial"}:
                failures.append(
                    f"{component_id}: noncommercial component has unsafe release scopes"
                )
        if scope in redistributed_scopes:
            documented_good_faith = status == documented_good_faith_status
            if status not in {"cleared", documented_good_faith_status}:
                failures.append(f"{component_id}: redistributed component is not cleared")
            if documented_good_faith:
                if not required_good_faith_fields.issubset(component):
                    failures.append(f"{component_id}: documented-good-faith component lacks provenance fields")
                if component.get("license") != "NOASSERTION":
                    failures.append(f"{component_id}: documented-good-faith component must retain NOASSERTION")
                if not isinstance(component.get("known_good_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", component["known_good_sha256"]):
                    failures.append(f"{component_id}: documented-good-faith hash contract is invalid")
                if not isinstance(component.get("known_good_size"), int) or component["known_good_size"] <= 0:
                    failures.append(f"{component_id}: documented-good-faith size contract is invalid")
                if "no firmware-specific licence found" not in str(component.get("license_finding", "")).lower():
                    failures.append(f"{component_id}: documented-good-faith licence finding is incomplete")
                if "established community precedent" not in str(component.get("redistribution_basis", "")).lower():
                    failures.append(f"{component_id}: documented-good-faith basis is incomplete")
            if component.get("license") in {"MIXED", "SEE-UPSTREAM", "UNDECLARED"} or (
                    component.get("license") == "NOASSERTION" and not documented_good_faith):
                failures.append(f"{component_id}: redistributed component has no SPDX conclusion")
            if component.get("download_location") == "NOASSERTION":
                failures.append(f"{component_id}: redistributed component lacks a download location")
            source_offer = component.get("source_offer")
            if (not documented_good_faith and (
                    not isinstance(source_offer, str) or
                    not re.fullmatch(r"https://[^\s]+", source_offer) or
                    source_offer.upper() in {"NOASSERTION", "NONE", "TBD", "TODO", "UNKNOWN"})):
                failures.append(f"{component_id}: source offer is unresolved")
        elif scope in local_scopes:
            if status != "not-redistributed":
                failures.append(f"{component_id}: local/external component has unsafe status")
        else:
            failures.append(f"{component_id}: unknown distribution scope")
        evidence = component.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(
                isinstance(item, str) and item.startswith("release/") for item in evidence):
            failures.append(f"{component_id}: public evidence list is missing")
        else:
            repository_root = path.parent.parent
            for item in evidence:
                evidence_path = repository_root / item
                if evidence_path.is_symlink() or not evidence_path.is_file():
                    failures.append(f"{component_id}: public evidence is unavailable: {item}")
        for key in ("name", "version", "license", "download_location", "source_offer"):
            value = component.get(key)
            if not isinstance(value, str) or not value:
                failures.append(f"{component_id}: {key} is missing")
            elif any(marker in value for marker in ("/home/", "192.168.", "/dev/tty")):
                failures.append(f"{component_id}: {key} contains a private value")
        if scope in local_scopes or release_scope in component_release_scopes:
            applicable.append(component)
    if failures:
        raise SystemExit("ERROR: public component gate failed: " + "; ".join(failures))
    return applicable


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, action="append", required=True)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--product-commit", required=True)
    parser.add_argument("--kernel-commit", required=True)
    parser.add_argument("--tooling-commit", required=True)
    parser.add_argument("--ui-commit", required=True)
    parser.add_argument("--components", type=Path, help="public component allowlist; defaults to release/components.json")
    parser.add_argument(
        "--release-scope", choices=sorted(RELEASE_SCOPES),
        default="commercially-unrestricted",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    if not RELEASE_ID.fullmatch(args.release_id):
        raise SystemExit("ERROR: release ID must be radar-puffin-vX.Y.Z")
    load_components(
        args.components or Path(__file__).resolve().parent.parent / "release/components.json",
        args.release_scope,
    )
    candidate, manifest = load_candidate(args.candidate)
    if candidate.get("status") != "PREPARED_NOT_FLASHED" and manifest.get("status") != "PREPARED_NOT_FLASHED":
        raise SystemExit("ERROR: candidate status is not PREPARED_NOT_FLASHED")
    for field, label in (("kernel_git_diff_sha256", "kernel"), ("tooling_git_diff_sha256", "tooling")):
        if field not in candidate:
            raise SystemExit(f"ERROR: candidate is missing clean-tree attestation: {field}")
        if candidate[field] not in ("", "0" * 64, EMPTY_DIFF_SHA256):
            raise SystemExit(f"ERROR: candidate {label} source is dirty")
    connectivity = manifest.get("connectivity", {})
    if connectivity.get("embedded_vendor_file_count") != 0:
        raise SystemExit("ERROR: candidate contains embedded vendor files")
    if connectivity.get("vendor_delivery") != "owner-device-local-extraction":
        raise SystemExit("ERROR: candidate vendor delivery policy is not owner-local")

    for artifact in args.artifact:
        if not artifact.is_file() or artifact.is_symlink():
            raise SystemExit(f"ERROR: artifact is not a regular file: {artifact}")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", artifact.name):
            raise SystemExit(f"ERROR: artifact name is not public-safe: {artifact.name}")
        if RUN_ID.search(artifact.name):
            raise SystemExit(f"ERROR: artifact name contains a private run ID: {artifact.name}")
        if artifact.stat().st_size < 1:
            raise SystemExit(f"ERROR: artifact is empty: {artifact}")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    records = [{"name": p.name, "sha256": sha256(p), "size": p.stat().st_size} for p in args.artifact]
    provenance = {
        "schema_version": 1,
        "release_id": args.release_id,
        "release_scope": args.release_scope,
        "platform": {"board": "radar_puffin", "architecture": "armv7", "kernel_line": "linux-6.1"},
        "sources": {
            "product": source("https://github.com/aslater3/LibreEcho", args.product_commit),
            "kernel": source("https://github.com/aslater3/LibreEcho-Linux-6.1", args.kernel_commit),
            "tooling": source("https://github.com/aslater3/LibreEcho-Platform", args.tooling_commit),
            "ui": source("https://github.com/aslater3/LibreEcho-UI", args.ui_commit),
        },
        "artifacts": records,
        "vendor_firmware": {"delivery": "owner-device-local-extraction", "embedded_file_count": 0, "redistributed": False},
    }
    (output / f"{args.release_id}-provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    with (output / f"{args.release_id}-SHA256SUMS").open("w") as stream:
        for record in records:
            stream.write(f"{record['sha256']}  {record['name']}\n")
    print(f"release_dir={output}")
    print(f"release_id={args.release_id}")
    print(f"artifact_count={len(records)}")


if __name__ == "__main__":
    main()
