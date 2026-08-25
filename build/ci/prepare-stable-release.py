#!/usr/bin/env python3
"""Prepare the exact public asset set for a signed stable release."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import tarfile
from pathlib import Path

COMMIT = re.compile(r"^[0-9a-f]{40}$")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
FEATURES = ("airplay2", "tts", "wakeword", "stt", "assistant")
WORKING_AMONET_COMMIT = "dfefe52f0eed7296012707cfff1f753b0ea33257"


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


def expect_hash(path: Path, expected: str, label: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256(path) != expected:
        fail(f"{label} hash mismatch: {path.name}")


def find_run(root: Path) -> Path:
    matches = [path.parent for path in root.rglob("CURRENT.candidate")]
    if len(matches) != 1:
        fail(f"expected one hosted run, found {len(matches)}")
    return matches[0]


def asset_record(path: Path) -> dict[str, object]:
    return {"name": path.name, "size": path.stat().st_size, "sha256": sha256(path)}


def initial_install_manifest(
    release_tag: str,
    boot: Path,
    public_key: Path,
    output: Path,
    amonet_repository: str,
    amonet_tag: str,
    amonet_commit: str,
) -> dict[str, object]:
    prefix = f"libreecho-{release_tag}"
    features = []
    for feature in FEATURES:
        payload = output / f"{prefix}-{feature}.squashfs"
        feature_manifest = output / f"{prefix}-{feature}.manifest.json"
        features.append({
            "name": feature,
            "payload": asset_record(payload),
            "manifest": asset_record(feature_manifest),
        })
    return {
        "schema": "libreecho-initial-install-v1",
        "release": release_tag,
        "board": "radar_puffin",
        "soc": "mt8163",
        "image_profile": "ota",
        "service_profile": "production",
        "boot": asset_record(boot),
        "ota_public_key": asset_record(public_key),
        "features": features,
        "amonet": {
            "repository": amonet_repository,
            "tag": amonet_tag,
            "commit": amonet_commit,
        },
    }


def write_initial_install_bundle(
    output: Path,
    release_tag: str,
    manifest: dict[str, object],
) -> Path:
    prefix = f"libreecho-{release_tag}"
    bundle = output / f"{prefix}-initial-install.tar"
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    members = ["manifest.json", f"{prefix}-boot.img", f"{prefix}-ota-public-key.hex"]
    for feature in FEATURES:
        members.extend((f"{prefix}-{feature}.squashfs", f"{prefix}-{feature}.manifest.json"))
    with tarfile.open(bundle, "w") as archive:
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        manifest_info.mode = 0o644
        archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        for name in members[1:]:
            path = output / name
            info = tarfile.TarInfo(name)
            info.size = path.stat().st_size
            info.mode = 0o644
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--product-root", type=Path, required=True)
    parser.add_argument("--product-commit", required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--release-notes", required=True)
    parser.add_argument("--amonet-repository", required=True)
    parser.add_argument("--amonet-tag", required=True)
    parser.add_argument("--amonet-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    if not COMMIT.fullmatch(args.product_commit):
        fail("product commit must be a full lowercase SHA")
    if not VERSION.fullmatch(args.release_version):
        fail("release version must be X.Y.Z")
    if not re.fullmatch(r"https://[^/]+/.+", args.amonet_repository) or not args.amonet_tag:
        fail("Amonet repository and tag are invalid")
    if args.amonet_commit != WORKING_AMONET_COMMIT:
        fail("Amonet commit is not the reviewed release commit")

    product = args.product_root.resolve()
    if not product.is_dir() or product.is_symlink():
        fail("product root is unsafe")
    release_tag = f"radar-puffin-v{args.release_version}"
    notes = product / args.release_notes
    installer = product / "tools/libreecho-install.py"
    regular(notes)
    regular(installer)

    run = find_run(args.artifact_root)
    candidate = read_kv(run / "CURRENT.candidate")
    provenance = read_kv(run / "provenance.txt")
    sources = read_kv(run / "release-source-commits.txt")
    request_path = run / "release-request.json"
    regular(request_path)
    request = json.loads(request_path.read_text(encoding="utf-8"))
    expected_request = {
        "schema": "libreecho-release-request-v1",
        "channel": "stable",
        "version": args.release_version,
        "release_tag": release_tag,
        "release_notes": args.release_notes,
    }
    if any(request.get(key) != value for key, value in expected_request.items()):
        fail("stable release request does not match the requested release")
    ssh_enabled = request.get("ssh_enabled", "0")
    if ssh_enabled not in {"0", "1"}:
        fail("stable release request has invalid ssh_enabled")

    if set(sources) != {"product", "platform", "linux", "ui"} or not all(COMMIT.fullmatch(value) for value in sources.values()):
        fail("source commit inventory is incomplete")
    if sources["product"] != args.product_commit:
        fail("artifact product commit does not match triggering workflow")
    if candidate.get("status") != "PREPARED_NOT_FLASHED":
        fail("candidate is not PREPARED_NOT_FLASHED")
    for field, expected in (
        ("public_release_mode", "1"),
        ("update_channel", "stable"),
        ("image_profile", "ota"),
        ("service_profile", "production"),
        ("feature_policy", "community-noncommercial"),
        ("ota_signing_mode", "local"),
    ):
        if candidate.get(field) != expected:
            fail(f"candidate has unexpected {field}")
    if candidate.get("ssh_enabled", "0") != ssh_enabled:
        fail("stable release request and candidate disagree on SSH")
    for field in ("dropbear_sha256", "dropbearkey_sha256"):
        value = candidate.get(field, "")
        if ssh_enabled == "1" and not re.fullmatch(r"[0-9a-f]{64}", value):
            fail(f"candidate is missing enabled SSH identity: {field}")
        if ssh_enabled == "0" and value:
            fail(f"disabled SSH candidate contains {field}")

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

    manifest_path = run / "manifest.json"
    verification = run / "verify.log"
    regular(manifest_path)
    regular(verification)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    connectivity = manifest.get("connectivity", {})
    if connectivity.get("embedded_vendor_file_count") != 0 or connectivity.get("vendor_delivery") != "owner-device-local-extraction":
        fail("candidate has an unsafe vendor connectivity policy")
    manifest_ssh = manifest.get("ssh", {})
    if not isinstance(manifest_ssh, dict) or bool(manifest_ssh.get("enabled")) != (ssh_enabled == "1"):
        fail("candidate SSH manifest does not match ssh_enabled")
    ssh_files = manifest_ssh.get("files", {})
    if ssh_enabled == "1":
        for field, path in (("dropbear_sha256", "sbin/dropbear"), ("dropbearkey_sha256", "sbin/dropbearkey")):
            record = ssh_files.get(path, {}) if isinstance(ssh_files, dict) else {}
            if record.get("sha256") != candidate[field]:
                fail(f"candidate SSH manifest identity mismatch: {field}")
    if "arm32_recovery_image_contract=PASS" not in verification.read_text(encoding="utf-8"):
        fail("independent image verification did not pass")

    ota = sorted(run.glob("*.ota.tar"))
    if len(ota) != 1:
        fail("stable release requires exactly one signed OTA bundle")
    regular(ota[0])
    expect_hash(ota[0], candidate.get("ota_bundle_sha256", ""), "OTA bundle")
    public_key = run / "ota-public-key.hex"
    regular(public_key)

    output = args.output_dir.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    prefix = f"libreecho-{release_tag}"
    copied: list[Path] = []

    def copy(source: Path, suffix: str, expected_hash: str = "", expected_size: str = "") -> None:
        regular(source)
        if expected_hash:
            expect_hash(source, expected_hash, suffix)
        if expected_size and source.stat().st_size != int(expected_size):
            fail(f"artifact size mismatch: {source.name}")
        target = output / f"{prefix}-{suffix}"
        shutil.copyfile(source, target)
        copied.append(target)

    copy(run / "boot.img", "boot.img", candidate.get("boot_image_sha256", ""))
    ota_target = output / f"{prefix}.ota.tar"
    shutil.copyfile(ota[0], ota_target)
    copied.append(ota_target)
    copy(public_key, "ota-public-key.hex")
    copy(notes, "release-notes.md")
    copy(installer, "installer.py")
    for feature in FEATURES:
        key = "airplay" if feature == "airplay2" else feature
        copy(run / "features" / f"{feature}.squashfs", f"{feature}.squashfs", candidate.get(f"{key}_payload_sha256", ""), candidate.get(f"{key}_payload_size", ""))
        copy(run / "features" / f"{feature}.manifest.json", f"{feature}.manifest.json", candidate.get(f"{key}_feature_manifest_sha256", ""))

    install_manifest = initial_install_manifest(release_tag, output / f"{prefix}-boot.img", output / f"{prefix}-ota-public-key.hex", output, args.amonet_repository, args.amonet_tag, args.amonet_commit)
    bundle = write_initial_install_bundle(output, release_tag, install_manifest)
    copied.append(bundle)

    records = [asset_record(path) for path in sorted(copied)]
    build_manifest = output / f"{prefix}-build.json"
    build_manifest.write_text(json.dumps({
        "schema": "libreecho-stable-release-v1",
        "release": release_tag,
        "board": "radar_puffin",
        "channel": "stable",
        "status": "PREPARED_NOT_FLASHED",
        "signed": True,
        "ssh_enabled": ssh_enabled == "1",
        "ssh": {
            "dropbear_sha256": candidate.get("dropbear_sha256", ""),
            "dropbearkey_sha256": candidate.get("dropbearkey_sha256", ""),
        },
        "ota_bundle": True,
        "hardware_accepted": False,
        "feature_policy": "community-noncommercial",
        "sources": sources,
        "artifacts": records,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    copied.append(build_manifest)

    sums = output / f"{prefix}-SHA256SUMS"
    sums.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in sorted(copied)), encoding="ascii")
    print(f"release_dir={output}")
    print(f"release_tag={release_tag}")
    print(f"asset_count={len(copied) + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
