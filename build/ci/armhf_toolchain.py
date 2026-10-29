#!/usr/bin/env python3
"""Authenticated ARMHF cross-toolchain lock, offline prefix materializer, verifier.

The Sendspin ARMHF cross-build's *compile-input* closure (an amd64 cross
compiler/binutils plus the arch-independent armhf glibc-2.39 development
sysroot) is an authenticated, hash-pinned artifact with its own identity
contract.  ``build/inputs/armhf-cross-toolchain.lock.json`` records the exact
package names, versions (with epochs), architectures, archive URLs, sizes and
SHA-256 values, and the authenticated upstream evidence (suite, ``Packages``
index digest, signed ``InRelease`` digest, signing-key fingerprint) for each of
the 30 archives.

This module has three jobs and no others:

* ``stage`` -- pre-validate every archive (size, SHA-256, and the *internal*
  ``control`` package identity) before any bytes are unpacked, then extract
  **only** each ``data.tar.*`` into a private sibling stage with a fail-closed
  member filter, merge the packages, write a portable receipt, and publish the
  prefix with an atomic no-replace rename.  No ``dpkg`` install, no maintainer
  script, no network, no recursive deletion of any supplied output.
* ``verify`` -- re-derive the expected full tree *from the archives* and compare
  it to the prefix; a receipt that agrees with a tampered prefix is still
  rejected because the receipt is never the source of identity.
* ``env`` -- print the consumer contract (``SYSROOT``/``CROSS_PREFIX``/
  ``LD_LIBRARY_PATH``) for the staged prefix.

Scope boundary: this is compile-input provenance only.  It deliberately leaves
``compile_sysroot.pinned`` false, makes no runtime/daemon closure claim, and
performs no QEMU/ARM execution.
"""
from __future__ import annotations

import argparse
import bz2
import ctypes
import errno
import gzip
import hashlib
import io
import json
import lzma
import os
import posixpath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import warnings
from pathlib import Path

LOCK_SCHEMA = "libreecho-armhf-cross-toolchain-lock/1"
RECEIPT_SCHEMA = "libreecho-armhf-toolchain-prefix-receipt/1"
TREE_SCHEMA = "libreecho-armhf-toolchain-tree/1"
RECEIPT_NAME = ".libreecho-armhf-toolchain-receipt.json"

TARGET = "arm-linux-gnueabihf"
CROSS_PREFIX_NAME = TARGET + "-"
LD_PATH = "usr/lib/x86_64-linux-gnu"

HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9A-Fa-f]{40}$")   # signing-key fingerprints carry case
ARCH = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")

MAX_ARCHIVES = 128
MAX_MEMBERS_PER_ARCHIVE = 200_000
MAX_MEMBER_SIZE = 1 << 30            # 1 GiB per member
MAX_PREFIX_BYTES = 4 << 30           # 4 GiB total extracted content
MAX_PATH_DEPTH = 64
MAX_PATH_LENGTH = 4096
ZSTD_TIMEOUT = 300

# Control fields that must be present and consistent for every archive.
_DEB_MAGIC = b"!<arch>\n"
_ALLOWED_MEMBER_TYPES = (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE,
                         tarfile.SYMTYPE)

# Descriptor flags for a directory whose identity is held for the duration of a
# materialization: never followed (``O_NOFOLLOW``) and never inherited.
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC


class ToolchainError(ValueError):
    """Any fail-closed lock/materialization/verification failure."""


# --------------------------------------------------------------------------
# Hashing and low-level container parsing
# --------------------------------------------------------------------------

def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_ar_members(path: Path) -> dict:
    """Return ``{member_name: bytes}`` for a POSIX ar (.deb) container."""
    with open(path, "rb") as handle:
        if handle.read(len(_DEB_MAGIC)) != _DEB_MAGIC:
            raise ToolchainError(f"not a Debian archive (bad magic): {path.name}")
        members = {}
        while True:
            header = handle.read(60)
            if not header:
                break
            if len(header) != 60 or header[58:60] != b"`\n":
                raise ToolchainError(f"malformed ar member header in {path.name}")
            name = header[0:16].decode("ascii", "replace").strip()
            try:
                size = int(header[48:58].decode("ascii").strip())
            except ValueError:
                raise ToolchainError(f"malformed ar member size in {path.name}")
            if size < 0:
                raise ToolchainError(f"negative ar member size in {path.name}")
            body = handle.read(size)
            if len(body) != size:
                raise ToolchainError(f"truncated ar member {name} in {path.name}")
            if size % 2:
                handle.read(1)
            members[name] = body
    return members


def _zstd_stream(data: bytes):
    try:
        from compression.zstd import ZstdFile    # Python 3.14+
        return ZstdFile(io.BytesIO(data))
    except ImportError:
        pass
    try:
        import zstandard                         # optional third-party
        return io.BytesIO(zstandard.ZstdDecompressor().decompress(data))
    except ImportError:
        pass
    tool = shutil.which("zstd")
    if not tool:
        raise ToolchainError(
            "no zstd backend is available to decompress a locked data.tar.zst "
            "archive; provide one of: Python >= 3.14 (stdlib compression.zstd), "
            "the zstandard Python module, or the zstd command-line tool on PATH")
    try:
        result = subprocess.run([tool, "-dc"], input=data, capture_output=True,
                                timeout=ZSTD_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ToolchainError(
            "zstd data archive decompression timed out after %d seconds"
            % ZSTD_TIMEOUT)
    if result.returncode != 0:
        raise ToolchainError("zstd data archive failed to decompress")
    return io.BytesIO(result.stdout)


def _open_tar(data: bytes, member_name: str):
    if member_name.endswith(".gz"):
        stream = gzip.GzipFile(fileobj=io.BytesIO(data))
    elif member_name.endswith(".xz"):
        stream = lzma.LZMAFile(io.BytesIO(data))
    elif member_name.endswith(".bz2"):
        stream = bz2.BZ2File(io.BytesIO(data))
    elif member_name.endswith(".zst") or member_name.endswith(".zstd"):
        stream = _zstd_stream(data)
    else:
        raise ToolchainError(f"unsupported data archive compression: {member_name}")
    try:
        return tarfile.open(fileobj=stream, mode="r:")
    except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
        raise ToolchainError(f"malformed tar member {member_name}: {exc}")


def _control_member(members: dict) -> str:
    names = [name for name in members if name.startswith("control.tar")]
    if len(names) != 1:
        raise ToolchainError("archive must carry exactly one control.tar member")
    return names[0]


def _data_member(members: dict) -> str:
    names = [name for name in members if name.startswith("data.tar")]
    if len(names) != 1:
        raise ToolchainError("archive must carry exactly one data.tar member")
    return names[0]


def archive_identity(deb_path: Path) -> dict:
    """Read the *internal* package identity from the control archive."""
    members = _read_ar_members(Path(deb_path))
    if members.get("debian-binary", b"").strip() != b"2.0":
        raise ToolchainError(f"archive is not a version 2.0 Debian package: {Path(deb_path).name}")
    control_name = _control_member(members)
    with _open_tar(members[control_name], control_name) as handle:
        member = next((m for m in handle.getmembers()
                       if m.name.lstrip("./") == "control"), None)
        if member is None:
            raise ToolchainError(f"control archive has no control file: {Path(deb_path).name}")
        stream = handle.extractfile(member)
        if stream is None:
            raise ToolchainError(f"control file has no content: {Path(deb_path).name}")
        raw = stream.read().decode("utf-8", "replace")
    fields = {}
    for line in raw.splitlines():
        if not line.strip():
            break
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip()] = value.strip()
    for field in ("Package", "Version", "Architecture"):
        if not fields.get(field):
            raise ToolchainError(f"control file is missing {field}: {Path(deb_path).name}")
    return {"package": fields["Package"], "version": fields["Version"],
            "architecture": fields["Architecture"]}


# --------------------------------------------------------------------------
# Lock
# --------------------------------------------------------------------------

def _reject_absolute(value: str, label: str) -> None:
    if value.startswith("/") or value.startswith("\\"):
        raise ToolchainError(f"{label} must not be an absolute path: {value!r}")


def load_lock(path) -> dict:
    """Validate the committed lock; any deviation fails closed."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ToolchainError(f"toolchain lock is missing or unsafe: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolchainError(f"toolchain lock is not valid JSON: {exc}")
    if not isinstance(document, dict) or document.get("schema") != LOCK_SCHEMA:
        raise ToolchainError("unsupported toolchain lock schema")
    archives = document.get("archives")
    if not isinstance(archives, list) or not archives:
        raise ToolchainError("toolchain lock contains no archives")
    if len(archives) > MAX_ARCHIVES:
        raise ToolchainError("toolchain lock declares too many archives")
    seen_names = set()
    seen_files = set()
    anchor = document.get("trust_anchor")
    anchor_fingerprint = (str(anchor.get("fingerprint", "")).upper()
                          if isinstance(anchor, dict) else None)
    for record in archives:
        if not isinstance(record, dict):
            raise ToolchainError("malformed toolchain lock record")
        name = record.get("name")
        for field in ("name", "version", "architecture", "filename", "url", "sha256"):
            value = record.get(field)
            if not isinstance(value, str) or not value:
                raise ToolchainError(f"lock record is missing {field}")
        filename = record["filename"]
        if Path(filename).name != filename or filename in (".", "..") or "\\" in filename:
            raise ToolchainError(f"invalid archive filename: {filename!r}")
        if not filename.endswith(".deb"):
            raise ToolchainError(f"invalid archive filename: {filename!r}")
        _reject_absolute(record["pool_path"] if isinstance(record.get("pool_path"), str)
                         else filename, "archive pool path")
        if not ARCH.fullmatch(record["architecture"]):
            raise ToolchainError(f"invalid architecture: {record['architecture']!r}")
        if not HEX64.fullmatch(record["sha256"]):
            raise ToolchainError(f"malformed archive digest for {name}")
        size = record.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ToolchainError(f"malformed archive size for {name}")
        if not record["url"].startswith("https://"):
            raise ToolchainError(f"archive url is not https for {name}")
        if record["url"].rsplit("/", 1)[-1] != filename:
            raise ToolchainError(f"archive url does not name its filename: {name}")
        auth = record.get("authentication")
        if not isinstance(auth, dict):
            raise ToolchainError(f"archive is missing authentication evidence: {name}")
        if not HEX64.fullmatch(str(auth.get("index_sha256", ""))):
            raise ToolchainError(f"malformed Packages index digest for {name}")
        if not HEX40.fullmatch(str(auth.get("signing_fingerprint", ""))):
            raise ToolchainError(f"malformed signing fingerprint for {name}")
        if (anchor_fingerprint is not None
                and str(auth.get("signing_fingerprint", "")).upper() != anchor_fingerprint):
            raise ToolchainError(
                f"archive signing fingerprint does not match the trust anchor: {name}")
        if not isinstance(auth.get("suite"), str) or not auth["suite"]:
            raise ToolchainError(f"archive is missing a suite: {name}")
        if name in seen_names:
            raise ToolchainError(f"duplicate package record: {name}")
        if filename in seen_files:
            raise ToolchainError(f"duplicate archive filename: {filename}")
        seen_names.add(name)
        seen_files.add(filename)
    return document


def verify_archives(lock: dict, archive_dir) -> list:
    """Re-hash every archive and check its internal identity; nothing is extracted."""
    archive_dir = Path(archive_dir)
    if archive_dir.is_symlink() or not archive_dir.is_dir():
        raise ToolchainError(f"archive directory is missing or unsafe: {archive_dir}")
    by_file = {record["filename"]: record for record in lock["archives"]}
    unexpected = sorted(
        entry.name for entry in archive_dir.iterdir()
        if entry.name.endswith(".deb") and entry.name not in by_file
    )
    if unexpected:
        raise ToolchainError("unrecorded archives present: " + ", ".join(unexpected))
    identities = []
    for record in sorted(lock["archives"],
                         key=lambda r: (r["name"], r["architecture"], r["version"])):
        path = archive_dir / record["filename"]
        if path.is_symlink() or not path.is_file():
            raise ToolchainError(f"archive is missing: {record['filename']}")
        if path.stat().st_size != record["size"]:
            raise ToolchainError(f"archive size mismatch: {record['filename']}")
        digest = sha256_file(path)
        if digest != record["sha256"]:
            raise ToolchainError(f"archive digest mismatch: {record['filename']}")
        identity = archive_identity(path)
        if (identity["package"] != record["name"]
                or identity["version"] != record["version"]
                or identity["architecture"] != record["architecture"]):
            raise ToolchainError(
                "archive package identity mismatch: %s (control reports %s %s %s)"
                % (record["filename"], identity["package"], identity["version"],
                   identity["architecture"])
            )
        identities.append({
            "name": record["name"], "version": record["version"],
            "architecture": record["architecture"], "sha256": digest,
            "size": record["size"],
        })
    return identities


# --------------------------------------------------------------------------
# Safe extraction
# --------------------------------------------------------------------------

def _normalize_member_name(name) -> str | None:
    """Return a safe relative POSIX path, ``None`` for the archive root, else raise."""
    if not isinstance(name, str) or not name:
        raise ToolchainError("archive member has an empty name")
    if "\x00" in name or "\\" in name:
        raise ToolchainError(f"archive member name is not a safe path: {name!r}")
    normalized = name
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized in ("", "."):
        return None
    if normalized.startswith("/"):
        raise ToolchainError(f"archive member is absolute: {name!r}")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ToolchainError(f"archive member escapes its destination: {name!r}")
    if len(parts) > MAX_PATH_DEPTH or len(normalized) > MAX_PATH_LENGTH:
        raise ToolchainError(f"archive member path is over bound: {name!r}")
    return normalized


def _resolve_link_target(name: str, target) -> str:
    """Validate a symlink target; return its in-root lexical resolution."""
    if not isinstance(target, str) or not target:
        raise ToolchainError(f"archive symlink has an empty target: {name}")
    if "\x00" in target or "\\" in target:
        raise ToolchainError(f"archive symlink target is not a safe path: {name} -> {target!r}")
    if target.startswith("/"):
        raise ToolchainError(f"archive symlink target is absolute: {name} -> {target!r}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    if resolved in (".", "..") or resolved.startswith("../") or resolved.startswith("/"):
        raise ToolchainError(f"archive symlink escapes the prefix: {name} -> {target!r}")
    return resolved


def extract_deb_data(deb_path, root, *, state=None) -> dict:
    """Extract only ``data.tar.*`` into ``root`` under a fail-closed member filter.

    Directory members merge (legitimate cross-package overlap); a file or
    symlink that collides with a different type, different content, or a
    different symlink target fails closed.  Maintainer scripts are never read
    or run.  ``state`` carries the cross-archive collision map and byte budget.
    """
    deb_path = Path(deb_path)
    root = Path(root)
    if state is None:
        state = {"paths": {}, "bytes": 0}
    members = _read_ar_members(deb_path)
    data_name = _data_member(members)
    with _open_tar(members[data_name], data_name) as handle:
        count = 0
        for member in handle:
            count += 1
            if count > MAX_MEMBERS_PER_ARCHIVE:
                raise ToolchainError(f"archive has too many members: {deb_path.name}")
            relative = _normalize_member_name(member.name)
            if relative is None:
                continue
            if relative == RECEIPT_NAME:
                raise ToolchainError("archive attempts to ship the receipt path")
            if member.type not in _ALLOWED_MEMBER_TYPES:
                raise ToolchainError(
                    f"archive ships a forbidden member type ({member.type!r}): {relative}")
            if member.size < 0 or member.size > MAX_MEMBER_SIZE:
                raise ToolchainError(f"archive member size is over bound: {relative}")
            destination = root / relative
            if member.isdir():
                _place_dir(destination, relative, member.mode & 0o777, state)
            elif member.issym():
                _resolve_link_target(relative, member.linkname)
                _place_link(destination, relative, member.linkname, state)
            else:
                _place_file(destination, relative, handle, member, state)
    return state


def _conflict(relative: str, detail: str):
    raise ToolchainError(f"conflicting package content at {relative}: {detail}")


def _place_dir(destination: Path, relative: str, mode: int, state: dict) -> None:
    existing = state["paths"].get(relative)
    if existing is not None and existing[0] != "d":
        _conflict(relative, "directory overlaps a non-directory")
    if os.path.lexists(destination) and not destination.is_dir():
        _conflict(relative, "directory overlaps an existing non-directory")
    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, mode)
    state["paths"][relative] = ("d", mode)


def _place_file(destination: Path, relative: str, handle, member, state: dict) -> None:
    existing = state["paths"].get(relative)
    if existing is not None and existing[0] != "f":
        _conflict(relative, "file overlaps a non-file")
    body = handle.extractfile(member)
    if body is None:
        raise ToolchainError(f"archive member has no content: {relative}")
    data = body.read()
    if len(data) != member.size:
        raise ToolchainError(f"archive member is truncated: {relative}")
    mode = member.mode & 0o777
    if os.path.lexists(destination):
        if destination.is_symlink() or not destination.is_file():
            _conflict(relative, "file overlaps an existing non-regular path")
        if destination.read_bytes() != data or (destination.stat().st_mode & 0o777) != mode:
            _conflict(relative, "duplicate file differs in content or mode")
        return
    state["bytes"] += len(data)
    if state["bytes"] > MAX_PREFIX_BYTES:
        raise ToolchainError("extracted prefix exceeds the byte budget")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "wb") as out:
        out.write(data)
    os.chmod(destination, mode)
    state["paths"][relative] = ("f", mode)


def _place_link(destination: Path, relative: str, target: str, state: dict) -> None:
    existing = state["paths"].get(relative)
    if existing is not None:
        if existing[0] != "l" or existing[1] != target:
            _conflict(relative, "symlink differs from an existing path")
        return
    if os.path.lexists(destination):
        _conflict(relative, "symlink overlaps an existing path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, destination)
    state["paths"][relative] = ("l", target)


# --------------------------------------------------------------------------
# Tree identity
# --------------------------------------------------------------------------

def _entry_type(path: Path) -> str:
    mode = os.lstat(path).st_mode
    if stat.S_ISLNK(mode):
        return "l"
    if stat.S_ISDIR(mode):
        return "d"
    if stat.S_ISREG(mode):
        return "f"
    raise ToolchainError(f"prefix contains a special file: {path}")


def tree_manifest(root, *, exclude=(), root_is_held=False) -> dict:
    """Derive a canonical, checkout-independent identity over a prefix tree.

    ``root_is_held`` marks ``root`` as a ``/proc/self/fd/<dirfd>`` adapter for a
    directory this process holds open.  The held descriptor, not the
    attacker-swappable pathname, is then authoritative, so the magic symlink at
    the root itself is expected and no existence re-resolution is performed.
    """
    root = Path(root)
    if root_is_held:
        if not root.is_dir():
            raise ToolchainError(f"prefix root is missing or unsafe: {root}")
    elif root.is_symlink() or not root.is_dir():
        raise ToolchainError(f"prefix root is missing or unsafe: {root}")
    exclude = set(exclude)
    entries = []
    digest = hashlib.sha256()
    digest.update((TREE_SCHEMA + "\n").encode())
    counts = {"files": 0, "directories": 0, "symlinks": 0}
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        names = sorted(dirnames + filenames)
        dirnames[:] = [d for d in dirnames if not (Path(current) / d).is_symlink()]
        for name in names:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if relative in exclude:
                continue
            kind = _entry_type(path)
            mode = os.lstat(path).st_mode & 0o777
            if kind == "l":
                target = os.readlink(path)
                _resolve_link_target(relative, target)
                entries.append({"path": relative, "type": "l", "mode": mode,
                                "target": target})
                digest.update(f"L\0{relative}\0{mode:o}\0{target}\n".encode())
                counts["symlinks"] += 1
            elif kind == "d":
                entries.append({"path": relative, "type": "d", "mode": mode})
                digest.update(f"D\0{relative}\0{mode:o}\n".encode())
                counts["directories"] += 1
            else:
                size = os.lstat(path).st_size
                sha = sha256_file(path)
                entries.append({"path": relative, "type": "f", "mode": mode,
                                "size": size, "sha256": sha})
                digest.update(f"F\0{relative}\0{mode:o}\0{size}\0{sha}\n".encode())
                counts["files"] += 1
    if not entries:
        raise ToolchainError(f"prefix tree is empty: {root}")
    entries.sort(key=lambda item: item["path"])
    return {"schema": TREE_SCHEMA, "sha256": digest.hexdigest(), "entries": entries, **counts}


def compare_manifests(expected: dict, actual: dict) -> None:
    """Raise on any missing, extra, type, mode, content or link-target drift."""
    expected_by_path = {item["path"]: item for item in expected["entries"]}
    actual_by_path = {item["path"]: item for item in actual["entries"]}
    missing = sorted(set(expected_by_path) - set(actual_by_path))
    extra = sorted(set(actual_by_path) - set(expected_by_path))
    if missing:
        raise ToolchainError(f"prefix is missing {len(missing)} entries, e.g. {missing[:5]}")
    if extra:
        raise ToolchainError(f"prefix has {len(extra)} unexpected entries, e.g. {extra[:5]}")
    for relative in sorted(expected_by_path):
        want = expected_by_path[relative]
        have = actual_by_path[relative]
        if want["type"] != have["type"]:
            raise ToolchainError(f"type drift at {relative}: {want['type']} != {have['type']}")
        if want["mode"] != have["mode"]:
            raise ToolchainError(f"mode drift at {relative}: {want['mode']:o} != {have['mode']:o}")
        if want["type"] == "f" and (want["sha256"] != have["sha256"]
                                    or want["size"] != have["size"]):
            raise ToolchainError(f"content drift at {relative}")
        if want["type"] == "l" and want["target"] != have["target"]:
            raise ToolchainError(f"symlink target drift at {relative}")


# --------------------------------------------------------------------------
# Receipt
# --------------------------------------------------------------------------

def _assert_no_absolute_paths(value, label="receipt") -> None:
    if isinstance(value, str):
        if value.startswith("/") or value.startswith("\\"):
            raise ToolchainError(f"{label} carries an absolute path: {value!r}")
    elif isinstance(value, dict):
        for item in value.values():
            _assert_no_absolute_paths(item, label)
    elif isinstance(value, list):
        for item in value:
            _assert_no_absolute_paths(item, label)


def build_receipt(lock_path, identities, manifest) -> dict:
    return {
        "schema": RECEIPT_SCHEMA,
        "lock_sha256": sha256_file(lock_path),
        "target": TARGET,
        "generated_by": "build/ci/armhf_toolchain.py",
        "archives": [
            {"name": item["name"], "version": item["version"],
             "architecture": item["architecture"], "sha256": item["sha256"],
             "size": item["size"]}
            for item in identities
        ],
        "tree": {
            "schema": manifest["schema"],
            "sha256": manifest["sha256"],
            "files": manifest["files"],
            "directories": manifest["directories"],
            "symlinks": manifest["symlinks"],
        },
    }


def load_receipt(prefix, *, lock_path, identities) -> dict:
    """Validate the receipt binds this lock, these archives and this tree."""
    path = Path(prefix) / RECEIPT_NAME
    if path.is_symlink() or not path.is_file():
        raise ToolchainError("prefix is missing its materialization receipt")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolchainError(f"receipt is not valid JSON: {exc}")
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise ToolchainError("unsupported receipt schema")
    _assert_no_absolute_paths(receipt)
    if receipt.get("lock_sha256") != sha256_file(lock_path):
        raise ToolchainError("receipt is bound to a different lock")
    expected = [{"name": item["name"], "version": item["version"],
                 "architecture": item["architecture"], "sha256": item["sha256"],
                 "size": item["size"]} for item in identities]
    if receipt.get("archives") != expected:
        raise ToolchainError("receipt archive identities do not match the archives")
    tree = receipt.get("tree")
    if not isinstance(tree, dict) or tree.get("schema") != TREE_SCHEMA:
        raise ToolchainError("receipt tree identity is malformed")
    if not HEX64.fullmatch(str(tree.get("sha256", ""))):
        raise ToolchainError("receipt tree digest is malformed")
    for key in ("files", "directories", "symlinks"):
        if not isinstance(tree.get(key), int) or isinstance(tree.get(key), bool):
            raise ToolchainError(f"receipt tree count is malformed: {key}")
    return receipt


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------

def _rename_noreplace(src_dir_fd: int, src_name: str, dst_dir_fd: int,
                      dst_name: str) -> None:
    """Atomically publish ``src_name`` as ``dst_name`` inside held descriptors.

    Both names are resolved relative to directory descriptors the caller opened
    with ``O_DIRECTORY|O_NOFOLLOW``; no component is ever re-resolved from a
    caller-supplied path string.  A destination parent swapped for a symlink
    after its descriptor was acquired therefore cannot redirect the rename.
    """
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError as exc:                                  # pragma: no cover
        raise ToolchainError(f"cannot load libc for atomic publication: {exc}")
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:                                   # pragma: no cover
        raise ToolchainError("renameat2 is unavailable; cannot publish atomically")
    rename_noreplace = 1
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                          ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(src_dir_fd, os.fsencode(src_name), dst_dir_fd,
                       os.fsencode(dst_name), rename_noreplace)
    if result != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise ToolchainError(
                f"refusing to overwrite an existing destination: {dst_name}")
        raise ToolchainError(
            f"publication rename failed: {errno.errorcode.get(code, code)}")


def _mkdir_confined(parent_fd: int, name: str, mode: int) -> None:
    """Create a directory relative to a held parent descriptor."""
    os.mkdir(name, mode, dir_fd=parent_fd)


def _open_confined_parent(destination: Path):
    """Open the destination's parent as a held, symlink-free descriptor chain.

    Every component is opened relative to the descriptor of its already-open
    parent with ``O_DIRECTORY|O_NOFOLLOW``.  A missing component is created
    *relative to that held descriptor* and then opened the same way, so a
    component swapped for a symlink between the create and the open fails
    closed rather than being followed.  This replaces the previous path-based
    ``lstat`` walk, which validated the ancestors but held no identity: a
    parent replaced by a symlink inside the window redirected publication.

    Returns ``(parent_fd, dest_name, held_fds, created)`` -- the descriptor of
    the destination parent, the destination's final name, every descriptor
    opened (the caller closes them), and the ``(parent_fd, name)`` pairs this
    call created (removable if the operation later fails).
    """
    destination = Path(destination)
    dest_name = destination.name
    if not dest_name or dest_name in (".", ".."):
        raise ToolchainError(f"invalid destination path: {destination}")
    if destination.is_absolute():
        parts = destination.parts[1:]
        base = os.open("/", _DIR_FLAGS)
    else:
        parts = destination.parts
        base = os.open(".", _DIR_FLAGS)
    held = [base]
    created = []
    fd = base
    try:
        for part in parts[:-1]:
            if part in ("", "."):
                continue
            if part == "..":
                child = os.open("..", _DIR_FLAGS, dir_fd=fd)
            else:
                try:
                    child = os.open(part, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=fd)
                except FileNotFoundError:
                    _mkdir_confined(fd, part, 0o777)
                    child = os.open(part, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=fd)
                    created.append((fd, part))
            held.append(child)
            fd = child
    except ToolchainError:
        _teardown_parent(held, created)
        raise
    except OSError as exc:
        _teardown_parent(held, created)
        raise ToolchainError(
            "destination parent is unsafe or unreadable: %s (%s)"
            % (destination, exc.strerror or exc.errno))
    return fd, dest_name, held, created


def _teardown_parent(held, created) -> None:
    """Remove directories this invocation created (if still empty); close fds."""
    for parent_fd, name in reversed(created):
        try:
            os.rmdir(name, dir_fd=parent_fd)
        except OSError:
            pass
    for fd in reversed(held):
        try:
            os.close(fd)
        except OSError:
            pass


def _owned_identity_matches(owned_stat, entry_stat) -> bool:
    """True when ``entry_stat`` is acceptable for an owned entry.

    With ``owned_stat`` (the ``fstat`` of a directory descriptor this invocation
    holds) the entry must be that same inode; without it, any entry is acceptable
    because the caller is walking a directory it already holds open.
    """
    return owned_stat is None or _same_identity(owned_stat, entry_stat)


def _remove_tree_at(parent_fd: int, name: str, *, owned_stat=None) -> str | None:
    """Recursively remove ``name`` inside a held parent, never following links.

    Removal is bound to positively identified inodes.  ``owned_stat`` is the
    ``fstat`` of a directory descriptor this invocation holds for ``name``; the
    entry, and every directory opened while recursing, must still be that inode
    both when it is checked and immediately after it is opened.  An entry renamed
    away, or replaced by another process's directory, file or symlink (including
    between the identity check and the ``open``), is left untouched so foreign
    data is never deleted.  Returns ``None`` when the named entry is gone,
    otherwise a human-readable diagnostic describing the residue left in place.
    """
    try:
        entry_stat = os.lstat(name, dir_fd=parent_fd)
    except FileNotFoundError:
        if owned_stat is not None:
            return ("the staging directory was moved out of the destination "
                    "parent; its original contents may remain as untracked residue")
        return None
    if not _owned_identity_matches(owned_stat, entry_stat):
        return ("refusing to remove %r: it is not the staging directory this "
                "invocation created" % name)
    if not stat.S_ISDIR(entry_stat.st_mode):
        try:
            os.unlink(name, dir_fd=parent_fd)
        except OSError:
            return "could not remove staging residue %r" % name
        return None
    try:
        child = os.open(name, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError:
        return "could not open staging residue %r for removal" % name
    notes = []
    try:
        opened = os.fstat(child)
        if ((opened.st_dev, opened.st_ino) != (entry_stat.st_dev, entry_stat.st_ino)
                or not _owned_identity_matches(owned_stat, opened)):
            return ("refusing to remove %r: it was replaced between the identity "
                    "check and the open, or is not this invocation's staging "
                    "directory" % name)
        for entry in os.scandir(child):
            if entry.is_dir(follow_symlinks=False):
                # The recursive call removes the directory itself (after its
                # contents), so there is nothing further to remove here; a
                # non-empty/residue result is reported verbatim.
                note = _remove_tree_at(child, entry.name)
                if note is not None:
                    notes.append(note)
            else:
                try:
                    os.unlink(entry.name, dir_fd=child)
                except OSError:
                    notes.append("could not remove staging residue %r/%r"
                                 % (name, entry.name))
    finally:
        os.close(child)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except OSError:
        notes.append("could not remove staging residue %r" % name)
    return "; ".join(notes) if notes else None


def _lexists_at(dir_fd: int, name: str) -> bool:
    try:
        os.lstat(name, dir_fd=dir_fd)
        return True
    except FileNotFoundError:
        return False


def _same_identity(a_stat, b_stat) -> bool:
    """True when ``b_stat`` is the same directory inode as ``a_stat``."""
    return (stat.S_ISDIR(b_stat.st_mode)
            and a_stat.st_dev == b_stat.st_dev and a_stat.st_ino == b_stat.st_ino)


def _is_detached(destination: Path, parent_fd: int, dest_name: str) -> bool:
    """True when the held publication is not reachable at the requested path.

    A success that lands in a directory detached from the requested path by an
    attacker is reported, never presented as the requested replacement.
    """
    try:
        held = os.lstat(dest_name, dir_fd=parent_fd)
    except FileNotFoundError:
        return True
    try:
        by_path = os.stat(destination, follow_symlinks=False)
    except OSError:
        return True
    return (held.st_dev, held.st_ino) != (by_path.st_dev, by_path.st_ino)


# --------------------------------------------------------------------------
# Stage and verify
# --------------------------------------------------------------------------

def stage(lock_path, archive_dir, prefix) -> dict:
    """Pre-validate, extract, receipt, and atomically publish an offline prefix.

    The destination parent is opened as a held ``O_DIRECTORY|O_NOFOLLOW``
    descriptor chain and every mkdir / stage creation / extraction / receipt /
    rename / cleanup operation is performed against those held descriptors (or a
    ``/proc/self/fd`` adapter for the freshly created stage), never by
    re-resolving the caller-supplied path.  A destination parent swapped for an
    external symlink after the descriptor is acquired therefore receives no
    bytes.  A success that lands in a directory detached from the requested path
    is reported via ``detached=True`` rather than presented as the requested
    replacement.
    """
    lock = load_lock(lock_path)
    identities = verify_archives(lock, archive_dir)
    destination = Path(prefix)
    parent_fd, dest_name, held, created = _open_confined_parent(destination)
    stage_name = ".armhf-toolchain-stage-" + secrets.token_hex(8)
    stage_fd = None
    stage_owned = None
    stage_created = False
    published = False
    detached = False
    try:
        if _lexists_at(parent_fd, dest_name):
            raise ToolchainError(
                f"refusing to overwrite an existing destination: {destination}")
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage_created = True
        stage_fd = os.open(stage_name, _DIR_FLAGS | os.O_NOFOLLOW, dir_fd=parent_fd)
        stage_owned = os.fstat(stage_fd)
        stage_root = Path("/proc/self/fd/%d" % stage_fd)
        state = {"paths": {}, "bytes": 0}
        for record in sorted(lock["archives"],
                             key=lambda r: (r["name"], r["architecture"], r["version"])):
            extract_deb_data(Path(archive_dir) / record["filename"], stage_root,
                             state=state)
        os.fchmod(stage_fd, 0o755)
        manifest = tree_manifest(stage_root, root_is_held=True)
        receipt = build_receipt(lock_path, identities, manifest)
        if _lexists_at(stage_fd, RECEIPT_NAME):
            raise ToolchainError("staging directory already carries a receipt")
        data = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
        receipt_fd = os.open(
            RECEIPT_NAME,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644,
            dir_fd=stage_fd)
        try:
            os.write(receipt_fd, data)
        finally:
            os.close(receipt_fd)
        # The stage entry must still be the directory this invocation created;
        # a swapped entry is refused before anything is published (the rename
        # itself is also confined, and the published name is re-checked below).
        try:
            stage_stat = os.lstat(stage_name, dir_fd=parent_fd)
        except FileNotFoundError:
            stage_stat = None
        if stage_stat is None or not _same_identity(os.fstat(stage_fd), stage_stat):
            raise ToolchainError("staging directory was replaced before publication")
        _rename_noreplace(parent_fd, stage_name, parent_fd, dest_name)
        # The published name must be our stage directory, not a swapped entry.
        try:
            dest_stat = os.lstat(dest_name, dir_fd=parent_fd)
        except FileNotFoundError:
            dest_stat = None
        if dest_stat is None or not _same_identity(os.fstat(stage_fd), dest_stat):
            # A foreign entry moved into the destination name by the rename is
            # left in place (never deleted to hide it) and reported as residue;
            # the run still fails closed, so the requested path is never claimed
            # to hold this invocation's stage.
            raise ToolchainError(
                "published destination was replaced during rename; a foreign "
                "entry may remain at the requested path")
        published = True
        detached = _is_detached(destination, parent_fd, dest_name)
    finally:
        if stage_fd is not None:
            try:
                os.close(stage_fd)
            except OSError:
                pass
        if not published:
            if stage_created:
                if stage_owned is not None:
                    note = _remove_tree_at(parent_fd, stage_name,
                                           owned_stat=stage_owned)
                else:
                    note = ("the staging directory identity was not held; leaving "
                            "any stage residue in place rather than removing it by "
                            "name")
                if note:
                    warnings.warn("staging cleanup left residue: %s" % note,
                                  RuntimeWarning, stacklevel=2)
            _teardown_parent(held, created)
        else:
            for fd in held:
                try:
                    os.close(fd)
                except OSError:
                    pass
    return {"prefix": str(destination), "archives": len(identities),
            "receipt": receipt, "detached": detached}


def _expected_tree(lock, archive_dir, parent: Path) -> dict:
    work = Path(tempfile.mkdtemp(prefix=".armhf-toolchain-verify-", dir=str(parent)))
    try:
        state = {"paths": {}, "bytes": 0}
        for record in sorted(lock["archives"],
                             key=lambda r: (r["name"], r["architecture"], r["version"])):
            extract_deb_data(Path(archive_dir) / record["filename"], work, state=state)
        return tree_manifest(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def verify(lock_path, archive_dir, prefix) -> dict:
    """Re-derive the expected tree from the archives and check the prefix against it."""
    lock = load_lock(lock_path)
    identities = verify_archives(lock, archive_dir)
    prefix = Path(prefix)
    if prefix.is_symlink() or not prefix.is_dir():
        raise ToolchainError(f"prefix is missing or unsafe: {prefix}")
    receipt = load_receipt(prefix, lock_path=lock_path, identities=identities)
    expected = _expected_tree(lock, archive_dir, prefix.parent)
    actual = tree_manifest(prefix, exclude={RECEIPT_NAME})
    compare_manifests(expected, actual)
    if receipt["tree"]["sha256"] != expected["sha256"]:
        raise ToolchainError("receipt tree digest does not match the archive-derived tree")
    for key in ("files", "directories", "symlinks"):
        if receipt["tree"][key] != expected[key]:
            raise ToolchainError(f"receipt tree count does not match: {key}")
    return {"prefix": str(prefix), "archives": len(identities),
            "tree_sha256": expected["sha256"], "files": expected["files"],
            "directories": expected["directories"], "symlinks": expected["symlinks"]}


def prefix_env(prefix) -> dict:
    """The consumer contract for a staged prefix; no shell eval is required."""
    prefix = str(prefix)
    return {
        "SYSROOT": prefix,
        "CROSS_PREFIX": prefix + "/usr/bin/" + CROSS_PREFIX_NAME,
        "LD_LIBRARY_PATH": prefix + "/" + LD_PATH,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    stage_parser = commands.add_parser("stage", help="materialize the prefix offline")
    stage_parser.add_argument("--lock", type=Path, required=True)
    stage_parser.add_argument("--archives", type=Path, required=True)
    stage_parser.add_argument("--prefix", type=Path, required=True)

    verify_parser = commands.add_parser("verify", help="verify a staged prefix")
    verify_parser.add_argument("--lock", type=Path, required=True)
    verify_parser.add_argument("--archives", type=Path, required=True)
    verify_parser.add_argument("--prefix", type=Path, required=True)

    env_parser = commands.add_parser("env", help="print the consumer prefix contract")
    env_parser.add_argument("--prefix", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "stage":
            result = stage(args.lock, args.archives, args.prefix)
            if result.get("detached"):
                print("WARNING: published into a directory detached from the "
                      "requested path (a destination ancestor changed during "
                      "materialization); the requested path is NOT claimed to "
                      "hold the prefix: %s" % result["prefix"], file=sys.stderr)
            print("armhf_toolchain_stage=PASS archives=%d prefix=%s lock_sha256=%s"
                  % (result["archives"], result["prefix"], result["receipt"]["lock_sha256"]))
            return 0
        if args.command == "verify":
            result = verify(args.lock, args.archives, args.prefix)
            print("armhf_toolchain_verify=PASS archives=%d tree_sha256=%s files=%d"
                  % (result["archives"], result["tree_sha256"], result["files"]))
            return 0
        for key, value in prefix_env(args.prefix).items():
            print(f"{key}={value}")
        return 0
    except (ToolchainError, json.JSONDecodeError, OSError) as error:
        print("ERROR: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
