#!/usr/bin/env python3
"""Generate a deterministic SPDX 2.3 JSON document from public release inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SHA256 = re.compile(r"^[0-9a-f]{64}$")
RELEASE_ID = re.compile(r"^radar-puffin-v[0-9]+\.[0-9]+\.[0-9]+$")


def package_id(name: str, version: str) -> str:
    digest = hashlib.sha256(f"{name}\0{version}".encode()).hexdigest()[:16]
    return f"SPDXRef-Package-{digest}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--created", required=True, help="UTC ISO-8601 timestamp")
    parser.add_argument("--components", type=Path, required=True, help="JSON array of public component records")
    parser.add_argument("--artifacts", type=Path, required=True, help="JSON array of {name, sha256, size}")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not RELEASE_ID.fullmatch(args.release_id):
        raise SystemExit("ERROR: invalid release ID")
    component_data = json.loads(args.components.read_text())
    if isinstance(component_data, dict):
        components = component_data.get("components")
    else:
        components = component_data
    artifacts = json.loads(args.artifacts.read_text())
    if not isinstance(components, list) or not isinstance(artifacts, list):
        raise SystemExit("ERROR: components and artifacts must be arrays")

    packages = []
    relationships = []
    for component in components:
        required = {
            "id", "name", "version", "license", "download_location",
            "release_status", "distribution_scope",
        }
        if not isinstance(component, dict) or not required.issubset(component):
            raise SystemExit(
                "ERROR: component requires id, name, version, license, download_location, "
                "release_status, and distribution_scope"
            )
        scope = component["distribution_scope"]
        status = component["release_status"]
        if scope in {"local-extraction-only", "external-user-supplied"}:
            if status != "not-redistributed":
                raise SystemExit(f"ERROR: non-redistributed component has unsafe status: {component['name']}")
            continue
        if scope not in {"source-release", "core-image", "separate-payload"}:
            raise SystemExit(f"ERROR: unknown component distribution scope: {component['name']}")
        if status != "cleared":
            raise SystemExit(f"ERROR: redistributed component is not cleared: {component['name']}")
        if any("/home/" in str(v) or "192.168." in str(v) for v in component.values()):
            raise SystemExit("ERROR: private value in component record")
        spdx_id = package_id(component["name"], component["version"])
        packages.append({
            "SPDXID": spdx_id,
            "name": component["name"],
            "versionInfo": component["version"],
            "downloadLocation": component["download_location"],
            "licenseConcluded": component["license"],
            "licenseDeclared": component["license"],
            "copyrightText": component.get("copyright", "NOASSERTION"),
        })
        relationships.append({"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": spdx_id})

    files = []
    for artifact in artifacts:
        if set(artifact) != {"name", "sha256", "size"} or not SHA256.fullmatch(artifact["sha256"]):
            raise SystemExit("ERROR: invalid artifact record")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", artifact["name"]):
            raise SystemExit("ERROR: unsafe artifact name")
        file_id = f"SPDXRef-File-{hashlib.sha256(artifact['name'].encode()).hexdigest()[:16]}"
        files.append({
            "SPDXID": file_id,
            "fileName": artifact["name"],
            "checksums": [{"algorithm": "SHA256", "checksumValue": artifact["sha256"]}],
            "licenseConcluded": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        })
        relationships.append({"spdxElementId": "SPDXRef-DOCUMENT", "relationshipType": "DESCRIBES", "relatedSpdxElement": file_id})

    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"LibreEcho {args.release_id} SBOM",
        "documentNamespace": f"https://libreecho.org/releases/{args.release_id}/sbom",
        "creationInfo": {"created": args.created, "creators": ["Organization: LibreEcho"]},
        "packages": packages,
        "files": files,
        "relationships": relationships,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print(f"sbom={args.output}")
    print(f"package_count={len(packages)}")
    print(f"artifact_count={len(files)}")


if __name__ == "__main__":
    main()
