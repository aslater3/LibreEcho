#!/usr/bin/env python3
"""Generate a deterministic SPDX 2.3 JSON document from public release inputs."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
RELEASE_ID = re.compile(r"^radar-puffin-v[0-9]+\.[0-9]+\.[0-9]+$")
RELEASE_SCOPES = {"commercially-unrestricted", "community-noncommercial"}


def package_id(name: str, version: str) -> str:
    digest = hashlib.sha256(f"{name}\0{version}".encode()).hexdigest()[:16]
    return f"SPDXRef-Package-{digest}"


def load_component_validator():
    path = Path(__file__).resolve().with_name("prepare-release.py")
    spec = importlib.util.spec_from_file_location("prepare_release", path)
    if spec is None or spec.loader is None:
        raise SystemExit("ERROR: release component validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_components


def load_provenance(
    path: Path | None, release_id: str, release_scope: str
) -> dict[str, str]:
    if path is None:
        return {}
    if not path.is_file() or path.is_symlink():
        raise SystemExit("ERROR: release provenance is unavailable")
    data = json.loads(path.read_text())
    if (not isinstance(data, dict) or data.get("release_id") != release_id or
            data.get("release_scope") != release_scope or
            not isinstance(data.get("sources"), dict)):
        raise SystemExit("ERROR: release provenance identity does not match the SBOM")
    commits: dict[str, str] = {}
    for name, source in data["sources"].items():
        if (not isinstance(name, str) or not isinstance(source, dict) or
                not isinstance(source.get("commit"), str) or
                not COMMIT.fullmatch(source["commit"])):
            raise SystemExit("ERROR: release provenance source commit is malformed")
        commits[name] = source["commit"]
    return commits


def resolve_version(component: dict[str, object], commits: dict[str, str]) -> str:
    version = component["version"]
    if not isinstance(version, str):
        raise SystemExit(f"ERROR: component version is malformed: {component['name']}")
    if not version.startswith("provenance:"):
        return version
    source_name = version.removeprefix("provenance:")
    commit = commits.get(source_name)
    if commit is None:
        raise SystemExit(
            f"ERROR: component source version is absent from provenance: {component['name']}"
        )
    return commit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--created", required=True, help="UTC ISO-8601 timestamp")
    parser.add_argument(
        "--release-scope", choices=sorted(RELEASE_SCOPES),
        default="commercially-unrestricted",
    )
    parser.add_argument("--components", type=Path, required=True,
                        help="versioned public component catalog")
    parser.add_argument("--provenance", type=Path,
                        help="sanitized release provenance used for source commit versions")
    parser.add_argument("--artifacts", type=Path, required=True,
                        help="JSON array of {name, sha256, size}")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not RELEASE_ID.fullmatch(args.release_id):
        raise SystemExit("ERROR: invalid release ID")

    load_components = load_component_validator()
    components = load_components(args.components, args.release_scope)
    artifacts = json.loads(args.artifacts.read_text())
    if not isinstance(artifacts, list):
        raise SystemExit("ERROR: artifacts must be an array")
    commits = load_provenance(args.provenance, args.release_id, args.release_scope)

    packages = []
    relationships = []
    for component in components:
        scope = component["distribution_scope"]
        if scope in {"local-extraction-only", "external-user-supplied"}:
            continue
        version = resolve_version(component, commits)
        spdx_id = package_id(str(component["name"]), version)
        packages.append({
            "SPDXID": spdx_id,
            "name": component["name"],
            "versionInfo": version,
            "downloadLocation": component["download_location"],
            "licenseConcluded": component["license"],
            "licenseDeclared": component["license"],
            "copyrightText": component.get("copyright", "NOASSERTION"),
        })
        relationships.append({
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": spdx_id,
        })

    files = []
    for artifact in artifacts:
        if (not isinstance(artifact, dict) or
                set(artifact) != {"name", "sha256", "size"} or
                not isinstance(artifact["sha256"], str) or
                not SHA256.fullmatch(artifact["sha256"])):
            raise SystemExit("ERROR: invalid artifact record")
        if not isinstance(artifact["name"], str) or not re.fullmatch(
            r"[A-Za-z0-9._-]+", artifact["name"]
        ):
            raise SystemExit("ERROR: unsafe artifact name")
        file_id = (
            "SPDXRef-File-" +
            hashlib.sha256(artifact["name"].encode()).hexdigest()[:16]
        )
        files.append({
            "SPDXID": file_id,
            "fileName": artifact["name"],
            "checksums": [{"algorithm": "SHA256", "checksumValue": artifact["sha256"]}],
            "licenseConcluded": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        })
        relationships.append({
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": file_id,
        })

    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"LibreEcho {args.release_id} {args.release_scope} SBOM",
        "documentNamespace": (
            f"https://libreecho.org/releases/{args.release_id}/"
            f"{args.release_scope}/sbom"
        ),
        "creationInfo": {
            "created": args.created,
            "creators": ["Organization: LibreEcho"],
        },
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
