#!/usr/bin/env python3
"""Prepare a verified LibreEcho initial-install bundle without device access."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

PHASE = "RELEASE_READY"
SCHEMA = "libreecho-initial-install-v1"
SHA256 = re.compile(r"[0-9a-f]{64}")
RELEASE = re.compile(r"radar-puffin-v[0-9]+\.[0-9]+\.[0-9]+")
PUBLIC_NAME = re.compile(r"[A-Za-z0-9._-]+")


class InstallerError(RuntimeError):
    """The bundle, cache, or resumable state does not meet the install contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_regular(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise InstallerError(f"unsafe or missing regular file: {path}")


def _safe_name(name: str) -> None:
    if not PUBLIC_NAME.fullmatch(name) or Path(name).name != name:
        raise InstallerError(f"unsafe public asset name: {name!r}")


def _exact_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise InstallerError(f"malformed {label}")
    return value


def _asset(value: Any, label: str) -> dict[str, Any]:
    record = _exact_keys(value, {"name", "size", "sha256"}, label)
    if (not isinstance(record["name"], str) or not isinstance(record["size"], int)
            or record["size"] < 1 or not isinstance(record["sha256"], str)):
        raise InstallerError(f"malformed {label}")
    _safe_name(record["name"])
    if not SHA256.fullmatch(record["sha256"]):
        raise InstallerError(f"malformed {label} digest")
    return record


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = _exact_keys(
        value,
        {"schema", "release", "board", "soc", "image_profile", "service_profile", "boot", "ota_public_key", "features", "amonet"},
        "install manifest",
    )
    if (manifest["schema"] != SCHEMA or not isinstance(manifest["release"], str)
            or not RELEASE.fullmatch(manifest["release"])
            or manifest["board"] != "radar_puffin" or manifest["soc"] != "mt8163"
            or manifest["image_profile"] != "ota" or manifest["service_profile"] != "production"):
        raise InstallerError("unsupported install manifest")
    manifest["boot"] = _asset(manifest["boot"], "boot record")
    manifest["ota_public_key"] = _asset(manifest["ota_public_key"], "OTA public-key record")
    features = manifest["features"]
    if not isinstance(features, list):
        raise InstallerError("malformed feature list")
    seen: set[str] = {manifest["boot"]["name"]}
    for feature in features:
        record = _exact_keys(feature, {"name", "payload", "manifest"}, "feature record")
        if not isinstance(record["name"], str) or not re.fullmatch(r"[a-z0-9._-]+", record["name"]):
            raise InstallerError("malformed feature name")
        record["payload"] = _asset(record["payload"], "feature payload")
        record["manifest"] = _asset(record["manifest"], "feature manifest")
        for asset in (record["payload"], record["manifest"]):
            if asset["name"] in seen:
                raise InstallerError("duplicate bundle member in manifest")
            seen.add(asset["name"])
    amonet = _exact_keys(manifest["amonet"], {"repository", "tag", "commit"}, "amonet record")
    if (not isinstance(amonet["repository"], str)
            or not re.fullmatch(r"https://[^/]+/.+", amonet["repository"])
            or not isinstance(amonet["tag"], str) or not amonet["tag"]
            or not isinstance(amonet["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", amonet["commit"])):
        raise InstallerError("malformed amonet record")
    return manifest


def _bundle_members(manifest: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    members: dict[str, dict[str, Any] | None] = {
        "manifest.json": None,
        manifest["boot"]["name"]: manifest["boot"],
        manifest["ota_public_key"]["name"]: manifest["ota_public_key"],
    }
    for feature in manifest["features"]:
        members[feature["payload"]["name"]] = feature["payload"]
        members[feature["manifest"]["name"]] = feature["manifest"]
    return members


def _checksums(path: Path, expected_names: set[str]) -> dict[str, str]:
    _safe_regular(path)
    records: dict[str, str] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if not match:
            raise InstallerError("malformed checksum entry")
        digest, name = match.groups()
        if name in records:
            raise InstallerError("duplicate checksum entry")
        records[name] = digest
    if set(records) != expected_names:
        raise InstallerError("checksum inventory mismatch")
    return records


def _copy_atomic(source: Path, destination: Path) -> None:
    _safe_regular(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    with source.open("rb") as input_stream, part.open("wb") as output_stream:
        shutil.copyfileobj(input_stream, output_stream)
        output_stream.flush()
        os.fsync(output_stream.fileno())
    os.replace(part, destination)


def _verify_bundle(bundle: Path, manifest: dict[str, Any], destination: Path) -> None:
    expected = _bundle_members(manifest)
    temporary = Path(tempfile.mkdtemp(prefix="bundle.", dir=destination.parent))
    try:
        with tarfile.open(bundle, "r") as archive:
            members = archive.getmembers()
            names: set[str] = set()
            for member in members:
                _safe_name(member.name)
                if not member.isreg() or member.name in names:
                    raise InstallerError("unsafe or duplicate bundle member")
                names.add(member.name)
            if names != set(expected):
                raise InstallerError("bundle member inventory mismatch")
            for member in members:
                output = temporary / member.name
                source = archive.extractfile(member)
                if source is None:
                    raise InstallerError("bundle member cannot be read")
                with source, output.open("wb") as stream:
                    shutil.copyfileobj(source, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                record = expected[member.name]
                if record is not None and (output.stat().st_size != record["size"] or _sha256(output) != record["sha256"]):
                    raise InstallerError("bundle member digest or size mismatch")
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(temporary, destination)
    except (OSError, tarfile.TarError) as error:
        raise InstallerError(f"cannot verify bundle: {error}") from error
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _state_path(state_root: Path, install_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", install_id):
        raise InstallerError("unsafe install id")
    return state_root / install_id / "state.json"


def _read_state(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallerError("malformed installer state") from error
    if (not isinstance(value, dict) or set(value) != {"phase", "release", "bundle_sha256"}
            or value["phase"] != PHASE or not isinstance(value["release"], str)
            or not RELEASE.fullmatch(value["release"])
            or not isinstance(value["bundle_sha256"], str) or not SHA256.fullmatch(value["bundle_sha256"])):
        raise InstallerError("malformed installer state")
    return value


def _write_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(path.name + ".part")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _prepare(release_dir: Path, cache_root: Path, release_tag: str) -> tuple[dict[str, Any], Path]:
    if not RELEASE.fullmatch(release_tag):
        raise InstallerError("invalid release tag")
    if not release_dir.is_dir() or release_dir.is_symlink():
        raise InstallerError("unsafe release directory")
    prefix = f"libreecho-{release_tag}"
    bundle = release_dir / f"{prefix}-initial-install.tar"
    checksums = release_dir / f"{prefix}-SHA256SUMS"
    _safe_regular(bundle)
    _safe_regular(checksums)
    with tarfile.open(bundle, "r") as archive:
        manifest_member = archive.getmember("manifest.json")
        if not manifest_member.isreg():
            raise InstallerError("manifest is not a regular bundle member")
        stream = archive.extractfile(manifest_member)
        if stream is None:
            raise InstallerError("manifest cannot be read")
        with stream:
            manifest = validate_manifest(json.load(stream))
    if manifest["release"] != release_tag:
        raise InstallerError("release tag does not match bundle manifest")
    expected = {
        bundle.name,
        f"{prefix}-installer.py",
        f"{prefix}-ota-public-key.hex",
        f"{prefix}-release-notes.md",
        manifest["boot"]["name"],
    }
    for feature in manifest["features"]:
        expected.update((feature["payload"]["name"], feature["manifest"]["name"]))
    records = _checksums(checksums, expected)
    for name, digest in records.items():
        candidate = release_dir / name
        _safe_regular(candidate)
        if _sha256(candidate) != digest:
            raise InstallerError(f"checksum mismatch: {name}")
    cache = cache_root / release_tag
    downloads = cache / "downloads"
    for name in expected:
        _copy_atomic(release_dir / name, downloads / name)
    _verify_bundle(downloads / bundle.name, manifest, cache / "bundle")
    return manifest, downloads / bundle.name


def install(
    release_dir: Path | str,
    cache_root: Path | str = Path.home() / ".cache/libreecho-installer",
    state_root: Path | str = Path.home() / ".local/state/libreecho-installer",
    install_id: str = "default",
    release_tag: str | None = None,
) -> dict[str, str]:
    release_dir = Path(release_dir)
    cache_root = Path(cache_root)
    state_root = Path(state_root)
    if release_tag is None:
        raise InstallerError("release tag is required")
    cache_root.mkdir(parents=True, exist_ok=True)
    lock_path = cache_root / ".lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallerError("another installer is already running") from error
        manifest, bundle = _prepare(release_dir, cache_root, release_tag)
        state = {"phase": PHASE, "release": manifest["release"], "bundle_sha256": _sha256(bundle)}
        _write_state(_state_path(state_root, install_id), state)
        return state


def resume(
    cache_root: Path | str = Path.home() / ".cache/libreecho-installer",
    state_root: Path | str = Path.home() / ".local/state/libreecho-installer",
    install_id: str = "default",
) -> dict[str, str]:
    state = _read_state(_state_path(Path(state_root), install_id))
    bundle = Path(cache_root) / state["release"] / "downloads" / f"libreecho-{state['release']}-initial-install.tar"
    _safe_regular(bundle)
    if _sha256(bundle) != state["bundle_sha256"]:
        raise InstallerError("cached bundle hash changed")
    return state


def status(state_root: Path | str = Path.home() / ".local/state/libreecho-installer", install_id: str = "default") -> dict[str, str]:
    path = _state_path(Path(state_root), install_id)
    if not path.exists():
        return {"phase": "MISSING"}
    return _read_state(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "resume", "status"))
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument("--release-tag")
    parser.add_argument("--cache-root", type=Path, default=Path.home() / ".cache/libreecho-installer")
    parser.add_argument("--state-root", type=Path, default=Path.home() / ".local/state/libreecho-installer")
    parser.add_argument("--install-id", default="default")
    args = parser.parse_args()
    try:
        if args.action == "install":
            if args.release_dir is None or args.release_tag is None:
                raise InstallerError("install requires --release-dir and --release-tag")
            result = install(args.release_dir, args.cache_root, args.state_root, args.install_id, args.release_tag)
        elif args.action == "resume":
            result = resume(args.cache_root, args.state_root, args.install_id)
        else:
            result = status(args.state_root, args.install_id)
    except InstallerError as error:
        raise SystemExit(f"ERROR: {error}") from error
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
