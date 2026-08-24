#!/usr/bin/env python3
"""Prepare a bounded unsigned dev release from a verified hosted build artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

COMMIT = re.compile(r"^[0-9a-f]{40}$")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
FEATURES = ("airplay2", "tts", "wakeword", "stt", "assistant")


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_kv(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        fail(f"missing or unsafe metadata: {path}")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def regular(path: Path) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size < 1:
        fail(f"missing, empty, or unsafe artifact: {path}")


def expect_hash(path: Path, expected: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256(path) != expected:
        fail(f"artifact hash mismatch: {path.name}")


def find_run(root: Path) -> Path:
    matches = [path.parent for path in root.rglob("CURRENT.candidate")]
    if len(matches) != 1:
        fail(f"expected one hosted run, found {len(matches)}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--product-commit", required=True)
    parser.add_argument("--release-kind", choices=("development", "nightly"), default="development")
    args = parser.parse_args()

    if not COMMIT.fullmatch(args.product_commit):
        fail("product commit must be a full lowercase SHA")
    run = find_run(args.artifact_root)
    candidate = read_kv(run / "CURRENT.candidate")
    provenance = read_kv(run / "provenance.txt")
    sources = read_kv(run / "release-source-commits.txt")
    manifest_path = run / "manifest.json"
    regular(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required_sources = {"product", "platform", "linux", "ui"}
    if set(sources) != required_sources or not all(COMMIT.fullmatch(value) for value in sources.values()):
        fail("source commit inventory is incomplete")
    if sources["product"] != args.product_commit:
        fail("artifact product commit does not match triggering workflow")
    if candidate.get("status") != "PREPARED_NOT_FLASHED":
        fail("candidate is not PREPARED_NOT_FLASHED")
    if candidate.get("public_release_mode") != "1" or candidate.get("update_channel") != "dev":
        fail("candidate is not a public dev build")
    for field, expected in (
        ("image_profile", "ota"),
        ("service_profile", "production"),
        ("feature_policy", "community-noncommercial"),
        ("ota_signing_mode", "github"),
    ):
        if candidate.get(field) != expected:
            fail(f"candidate has unexpected {field}")
    ssh_enabled = candidate.get("ssh_enabled", "0")
    if ssh_enabled not in {"0", "1"}:
        fail("candidate has invalid ssh_enabled")
    ssh_manifest = manifest.get("ssh", {})
    if not isinstance(ssh_manifest, dict) or bool(ssh_manifest.get("enabled")) != (ssh_enabled == "1"):
        fail("candidate SSH manifest does not match ssh_enabled")
    ssh_files = ssh_manifest.get("files", {}) if isinstance(ssh_manifest, dict) else {}
    for field in ("dropbear_sha256", "dropbearkey_sha256"):
        value = candidate.get(field, "")
        if ssh_enabled == "1" and not re.fullmatch(r"[0-9a-f]{64}", value):
            fail(f"candidate is missing enabled SSH identity: {field}")
        if ssh_enabled == "0" and value:
            fail(f"disabled SSH candidate contains {field}")
    if ssh_enabled == "1":
        for field, path in (("dropbear_sha256", "sbin/dropbear"), ("dropbearkey_sha256", "sbin/dropbearkey")):
            record = ssh_files.get(path, {}) if isinstance(ssh_files, dict) else {}
            if record.get("sha256") != candidate[field]:
                fail(f"candidate SSH manifest identity mismatch: {field}")
    if candidate.get("ota_bundle") or candidate.get("ota_bundle_sha256"):
        fail("unsigned dev candidate unexpectedly contains an OTA bundle")
    if candidate.get("product_git_head") != sources["product"]:
        fail("candidate product identity mismatch")
    if candidate.get("tooling_git_head") != sources["platform"]:
        fail("candidate platform identity mismatch")
    if candidate.get("ui_commit") != sources["ui"]:
        fail("candidate UI identity mismatch")
    kernel_prefix = provenance.get("kernel_git_head", "")
    if not re.fullmatch(r"[0-9a-f]{12,40}", kernel_prefix) or not sources["linux"].startswith(kernel_prefix):
        fail("candidate Linux identity mismatch")
    for field in ("product_git_diff_sha256", "tooling_git_diff_sha256", "kernel_git_diff_sha256", "ui_diff_sha256"):
        if candidate.get(field, provenance.get(field)) not in ("", EMPTY_SHA256):
            fail(f"candidate source is dirty: {field}")
    connectivity = manifest.get("connectivity", {})
    if connectivity.get("embedded_vendor_file_count") != 0:
        fail("candidate contains embedded vendor connectivity files")
    if connectivity.get("vendor_delivery") != "owner-device-local-extraction":
        fail("candidate has an unsafe vendor connectivity policy")

    verification = run / "verify.log"
    regular(verification)
    verify_text = verification.read_text(encoding="utf-8")
    if "arm32_recovery_image_contract=PASS" not in verify_text or "status=PREPARED_NOT_FLASHED" not in verify_text:
        fail("independent image verification did not pass")

    output = args.output_dir.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    source_set_id = hashlib.sha256(
        ("\n".join(sources[name] for name in ("product", "platform", "linux", "ui")) + "\n").encode()
    ).hexdigest()[:16]
    prefix = f"libreecho-radar-puffin-build-{args.product_commit[:7]}-{source_set_id}"
    release_tag_prefix = "radar-puffin-nightly" if args.release_kind == "nightly" else "radar-puffin-build"
    copied: list[Path] = []

    def copy(source: Path, suffix: str, expected_hash: str, expected_size: str = "") -> None:
        regular(source)
        expect_hash(source, expected_hash)
        if expected_size and source.stat().st_size != int(expected_size):
            fail(f"artifact size mismatch: {source.name}")
        target = output / f"{prefix}-{suffix}"
        shutil.copyfile(source, target)
        copied.append(target)

    copy(run / "boot.img", "boot.img", candidate.get("boot_image_sha256", ""))
    for feature in FEATURES:
        key = "airplay" if feature == "airplay2" else feature
        copy(
            run / "features" / f"{feature}.squashfs",
            f"{feature}.squashfs",
            candidate.get(f"{key}_payload_sha256", ""),
            candidate.get(f"{key}_payload_size", ""),
        )
        copy(
            run / "features" / f"{feature}.manifest.json",
            f"{feature}.manifest.json",
            candidate.get(f"{key}_feature_manifest_sha256", ""),
        )
    verification_target = output / f"{prefix}-verification.txt"
    shutil.copyfile(verification, verification_target)
    copied.append(verification_target)

    records = [
        {"name": path.name, "size": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(copied)
    ]
    artifact_set_id = hashlib.sha256(json.dumps({
        "sources": [sources[name] for name in ("product", "platform", "linux", "ui")],
        "artifacts": records,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
    release_manifest = output / f"{prefix}-build.json"
    release_manifest.write_text(json.dumps({
        "schema": "libreecho-development-build-v1",
        "build_id": f"{source_set_id}-{artifact_set_id}",
        "source_set_id": source_set_id,
        "artifact_set_id": artifact_set_id,
        "board": "radar_puffin",
        "channel": "dev",
        "kind": args.release_kind,
        "ssh_enabled": ssh_enabled == "1",
        "ssh": {
            "dropbear_sha256": candidate.get("dropbear_sha256", ""),
            "dropbearkey_sha256": candidate.get("dropbearkey_sha256", ""),
        },
        "status": "PREPARED_NOT_FLASHED",
        "signed": False,
        "ota_bundle": False,
        "hardware_accepted": False,
        "feature_policy": "community-noncommercial",
        "sources": {
            name: {
                "repository": {
                    "product": "https://github.com/aslater3/LibreEcho",
                    "platform": "https://github.com/aslater3/LibreEcho-Platform",
                    "linux": "https://github.com/aslater3/LibreEcho-Linux-6.1",
                    "ui": "https://github.com/aslater3/LibreEcho-UI",
                }[name],
                "commit": commit,
            }
            for name, commit in sources.items()
        },
        "artifacts": records,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    copied.append(release_manifest)

    sums = output / f"{prefix}-SHA256SUMS"
    sums.write_text("".join(
        f"{sha256(path)}  {path.name}\n" for path in sorted(copied)
    ), encoding="ascii")
    print(f"release_dir={output}")
    print(f"release_tag={release_tag_prefix}-{args.product_commit[:7]}-{source_set_id}-{artifact_set_id}")
    print(f"release_prefix={prefix}")
    print(f"source_set_id={source_set_id}")
    print(f"artifact_set_id={artifact_set_id}")
    print(f"asset_count={len(copied) + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
