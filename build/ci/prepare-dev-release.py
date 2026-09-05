#!/usr/bin/env python3
"""Prepare a bounded unsigned dev release from a verified hosted build artifact."""
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


def prepare_complete_initial_install(
    run: Path,
    output: Path,
    candidate: dict[str, str],
    sources: dict[str, str],
    verification: Path,
    ota: Path,
    source_set_id: str,
    artifact_set_id: str,
    release_kind: str,
) -> tuple[str, int]:
    """Add the complete one-shot asset set to a dev or nightly release."""
    product = Path(__file__).resolve().parents[2]
    installer = product / "tools" / "libreecho-install.py"
    ota_key = run / "ota-public-key.hex"
    if not ota_key.is_file():
        fail("nightly candidate is missing ota-public-key.hex")
    if not installer.is_file():
        fail("Product installer source is missing")
    tag_prefix = "radar-puffin-nightly" if release_kind == "nightly" else "radar-puffin-build"
    release_tag = f"{tag_prefix}-{candidate['product_git_head'][:7]}-{source_set_id}-{artifact_set_id}"
    prefix = f"libreecho-{release_tag}"
    sources_dir = run / "features"
    if not sources_dir.is_dir():
        fail("nightly candidate is missing its features directory")
    files: list[tuple[str, Path]] = [
        (f"{prefix}-boot.img", run / "boot.img"),
        (f"{prefix}.ota.tar", ota),
        (f"{prefix}-installer.py", installer),
        (f"{prefix}-ota-public-key.hex", ota_key),
        (f"{prefix}-verification.txt", verification),
        (f"{prefix}-run-one-shot.sh", product / "tools" / "run-one-shot.sh"),
    ]
    for feature in FEATURES:
        files.extend((
            (f"{prefix}-{feature}.squashfs", sources_dir / f"{feature}.squashfs"),
            (f"{prefix}-{feature}.manifest.json", sources_dir / f"{feature}.manifest.json"),
        ))
    for target_name, source in files:
        regular(source)
        expected = ""
        if target_name.endswith("-boot.img"):
            expected = candidate.get("boot_image_sha256", "")
        elif target_name.endswith(".ota.tar"):
            expected = candidate.get("ota_bundle_sha256", "")
        elif target_name.endswith("-installer.py"):
            expected = sha256(source)
        elif target_name.endswith("-ota-public-key.hex"):
            expected = sha256(source)
        elif target_name.endswith("-verification.txt"):
            expected = sha256(source)
        elif target_name.endswith("-run-one-shot.sh"):
            expected = sha256(source)
        else:
            if target_name.endswith(".squashfs"):
                feature = target_name.removeprefix(prefix + "-").removesuffix(".squashfs")
                key = "airplay" if feature == "airplay2" else feature
                expected = candidate.get(f"{key}_payload_sha256", "")
            else:
                feature = target_name.removeprefix(prefix + "-").removesuffix(".manifest.json")
                key = "airplay" if feature == "airplay2" else feature
                expected = candidate.get(f"{key}_feature_manifest_sha256", "")
        expect_hash(source, expected)
        shutil.copyfile(source, output / target_name)

    records = {
        name: {"name": name, "size": (output / name).stat().st_size, "sha256": sha256(output / name)}
        for name, _ in files
    }
    manifest = {
        "schema": "libreecho-initial-install-v1",
        "release": release_tag,
        "board": "radar_puffin",
        "soc": "mt8163",
        "image_profile": "ota",
        "service_profile": "production",
        "boot": records[f"{prefix}-boot.img"],
        "ota_public_key": records[f"{prefix}-ota-public-key.hex"],
        "features": [
            {"name": feature, "payload": records[f"{prefix}-{feature}.squashfs"],
             "manifest": records[f"{prefix}-{feature}.manifest.json"]}
            for feature in FEATURES
        ],
        "amonet": {
            "repository": "https://github.com/aslater3/amonet-k32",
            "tag": "dfefe52f0eed7296012707cfff1f753b0ea33257",
            "commit": "dfefe52f0eed7296012707cfff1f753b0ea33257",
        },
    }
    bundle = output / f"{prefix}-initial-install.tar"
    bundle_members = [
        f"{prefix}-boot.img", f"{prefix}-ota-public-key.hex",
        *[f"{prefix}-{feature}.{suffix}" for feature in FEATURES for suffix in ("squashfs", "manifest.json")],
    ]
    manifest_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with tarfile.open(bundle, "w") as archive:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(manifest_bytes))
        for name in bundle_members:
            path = output / name
            info = tarfile.TarInfo(name)
            info.size = path.stat().st_size
            info.mode = 0o644
            with path.open("rb") as stream:
                archive.addfile(info, stream)

    build_manifest = {
        "schema": "libreecho-development-build-v1",
        "build_id": f"{source_set_id}-{artifact_set_id}",
        "source_set_id": source_set_id,
        "artifact_set_id": artifact_set_id,
        "board": "radar_puffin",
        "channel": "dev",
        "kind": release_kind,
        "status": "PREPARED_NOT_FLASHED",
        "signed": True,
        "ota_bundle": True,
        "hardware_accepted": False,
        "feature_policy": "community-noncommercial",
        "sources": sources,
        "artifacts": [records[name] for name, _ in files],
    }
    (output / f"{prefix}-build.json").write_text(json.dumps(build_manifest, indent=2, sort_keys=True) + "\n")
    notes = output / f"{prefix}-release-notes.md"
    notes.write_text(
        f"# LibreEcho {release_kind} {release_tag}\n\n"
        "This is a signed development build for controlled hardware testing. "
        "It includes the complete one-shot installer asset set. Status: "
        "PREPARED_NOT_FLASHED; no hardware acceptance is implied.\n"
    )
    all_files = sorted(path for path in output.iterdir() if path.is_file())
    sums = output / f"{prefix}-SHA256SUMS"
    sums.write_text("".join(f"{sha256(path)}  {path.name}\n" for path in all_files), encoding="ascii")
    return release_tag, len(all_files) + 1


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
    signed = candidate.get("ota_signing_mode") == "local"
    for field, expected in (
        ("image_profile", "ota"),
        ("service_profile", "production"),
        ("feature_policy", "community-noncommercial"),
    ):
        if candidate.get(field) != expected:
            fail(f"candidate has unexpected {field}")
    if candidate.get("ota_signing_mode") not in {"github", "local"}:
        fail("candidate has unexpected ota_signing_mode")
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
    ota_bundles = sorted(run.glob("*.ota.tar"))
    if signed:
        if len(ota_bundles) != 1:
            fail("signed dev candidate requires exactly one OTA bundle")
        expect_hash(ota_bundles[0], candidate.get("ota_bundle_sha256", ""))
    elif candidate.get("ota_bundle") or candidate.get("ota_bundle_sha256") or ota_bundles:
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
    if signed:
        regular(ota_bundles[0])
        expect_hash(ota_bundles[0], candidate.get("ota_bundle_sha256", ""))
        ota_target = output / f"{prefix}.ota.tar"
        shutil.copyfile(ota_bundles[0], ota_target)
        copied.append(ota_target)
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
        "signed": signed,
        "ota_bundle": signed,
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
    if args.release_kind in {"development", "nightly"} and signed:
        if len(ota_bundles) != 1:
            fail(f"{args.release_kind} one-shot artifact requires exactly one signed OTA")
        for path in output.iterdir():
            if path.is_file():
                path.unlink()
        release_tag, asset_count = prepare_complete_initial_install(
            run, output, candidate, sources, verification, ota_bundles[0],
            source_set_id, artifact_set_id, args.release_kind,
        )
        print(f"release_dir={output}")
        print(f"release_tag={release_tag}")
        print(f"release_prefix=libreecho-{release_tag}")
        print(f"source_set_id={source_set_id}")
        print(f"artifact_set_id={artifact_set_id}")
        print(f"asset_count={asset_count}")
        print("signed=1")
        print("ota_bundle=1")
        return 0
    print(f"release_dir={output}")
    print(f"release_tag={release_tag_prefix}-{args.product_commit[:7]}-{source_set_id}-{artifact_set_id}")
    print(f"release_prefix={prefix}")
    print(f"source_set_id={source_set_id}")
    print(f"artifact_set_id={artifact_set_id}")
    print(f"asset_count={len(copied) + 1}")
    print(f"signed={int(signed)}")
    print(f"ota_bundle={int(signed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
