#!/usr/bin/env python3
"""Fail-closed validation and staging for the public dependency inventory.

Two jobs live here:

* ``load()`` is the schema gate. For a Sendspin pinned source it requires an
  immutable commit, a SHA-256 archive digest, an archive URL that embeds that
  exact commit, and submodules that are relative, normalized, traversal-free,
  duplicate-free, bounded, and pinned the same way.

* ``stage()`` / ``stage_sendspin()`` materialize those sources with a bounded
  downloader (curl, else wget, else urllib), verify the *observed* archive
  SHA-256 before extracting anything, and lay each source down as a fresh tree
  via a private temp directory plus atomic rename -- never by merging into an
  existing tree. An explicitly selected ``--archive-dir`` verified local pool is
  exclusive for commit-pinned archives in *both* routes: a cache miss fails
  closed instead of reaching the network. ``stage_sendspin()`` writes a receipt
  whose content hashes (archive digest + tree digest) are the staging contract
  other tooling can consume; a declared-but-unverified digest is never reported
  as proof.

* ``stage_sendspin()`` builds a source *and every one of its declared
  submodules* inside one private temp tree, then compares/publishes the whole
  component atomically and records the hashes of the tree that actually landed
  on disk. A source entry's ``tree_sha256`` therefore covers its full tree
  **including** its declared submodule subtrees (each submodule also gets its
  own subtree entry). That full-tree scope is deliberately distinct from the
  Platform SDK verifier, which excludes submodule subtrees and byte-checks the
  re-extracted parent with ``--compare-tree`` instead of trusting this receipt.

* When the inventory declares an applied ``patch_inventory`` mirror (the
  Platform-owned Sendspin patches), *both* staging routes first authenticate the
  exact Platform ``owner`` and the lock/mirror agreement, require an explicit
  Platform SOURCE.lock and ``patch_dir`` (there is no implicit default and no
  arbitrary checkout is consulted), and enumerate the patch directory closed
  (dotfiles, subdirectories and symlinks are refused). Each patch's bytes are
  read once through a no-follow descriptor into a frozen digest-verified buffer,
  and it is that buffer — never a re-opened mutable path — that is applied, in
  declared order and exactly once, to the *freshly archive-derived* tree, before
  the whole staged tree is confined-checked and published. A route that declares
  patches therefore fails closed *before* creating an output path rather than
  materializing a pristine tree under a patched inventory.
"""
from __future__ import annotations
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import NamedTuple
from urllib.parse import urlparse
from urllib.request import url2pathname

SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
SCHEMA = "libreecho-public-inputs-v1"
VENDORED_PREFIX = "vendored://"
RECEIPT_SCHEMA = "libreecho-sendspin-product-stage-receipt/1"
PATCH_SCHEMA = "libreecho-sendspin-patch-inventory/1"
SENDSPIN_PREFIX = "sendspin-"
MAX_SUBMODULES_PER_SOURCE = 64
DOWNLOAD_TIMEOUT = 600
DOWNLOAD_USER_AGENT = "LibreEcho-public-build/1"
# The reviewed Sendspin patch inventory is owned by the Platform repository
# (tools/mt8163-arm32/sendspin/patches); the Product inventory mirrors the
# Platform SOURCE.lock declaration but never owns the bytes.
SENDSPIN_PATCH_OWNER = "LibreEcho-Platform"


class VerifiedPatch(NamedTuple):
    """A declared patch whose bytes were read exactly once and digest-verified.

    The *verified bytes* -- not a mutable path into the source patch directory --
    are what get applied and recorded. A concurrent swap of the on-disk patch
    after validation therefore has no effect: the transform and the receipt both
    come from this frozen buffer, never from a re-opened path.
    """

    file: str
    sha256: str
    data: bytes


def _read_regular_file_nofollow(path: Path) -> bytes:
    """Read a regular file once through a held descriptor that never follows a link.

    The patch source directory is trusted-but-mutable, so the bytes must be
    captured atomically and safely: ``O_NOFOLLOW`` refuses to open a symlink (a
    link could be swapped to point at an arbitrary file between the directory
    listing and the read), ``fstat`` on the held descriptor re-confirms a regular
    file (no fifo/device), and the caller keeps the returned buffer.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open patch safely (missing, symlink, or unreadable): {path}: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"patch is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1 << 20)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_submodule_path(component: str, path: object, seen: set) -> str:
    """Return ``path`` if it is a safe relative staging path, else raise.

    A submodule path is joined onto a component destination during staging, so
    it must be a normalized POSIX relative path with no absolute prefix, no
    backslash, no empty/``.``/``..`` component, and no duplicate within the
    record. Anything else could escape or shadow the staging root.
    """
    if not isinstance(path, str) or not path:
        raise ValueError(f"sendspin submodule path is empty: {component}")
    if "\x00" in path or "\\" in path:
        raise ValueError(
            f"sendspin submodule path is not a posix relative path: {component}: {path!r}"
        )
    if path.startswith("/") or PurePosixPath(path).is_absolute():
        raise ValueError(f"sendspin submodule path is absolute: {component}: {path!r}")
    if path != posixpath.normpath(path):
        raise ValueError(f"sendspin submodule path is not normalized: {component}: {path!r}")
    if any(part in ("", ".", "..") for part in path.split("/")):
        raise ValueError(f"sendspin submodule path escapes its destination: {component}: {path!r}")
    if path in seen:
        raise ValueError(f"duplicate sendspin submodule path: {component}: {path!r}")
    seen.add(path)
    return path


def _submodules(record: dict) -> list:
    subs = record.get("submodules", [])
    if not isinstance(subs, list):
        raise ValueError(f"sendspin submodules must be a list: {record.get('name')}")
    if len(subs) > MAX_SUBMODULES_PER_SOURCE:
        raise ValueError(
            f"sendspin record declares too many submodules: {record.get('name')}"
        )
    return subs


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA or not isinstance(data.get("inputs"), list):
        raise ValueError("invalid public input schema")
    names = set()
    for item in data["inputs"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError("malformed input record")
        if item["name"] in names:
            raise ValueError("duplicate input name")
        names.add(item["name"])
        for key in ("url", "sha256", "kind", "license", "redistribution"):
            if not isinstance(item.get(key), str):
                raise ValueError(f"missing input field: {key}")
        if item["kind"] == "source-git":
            if not item["url"].startswith("https://"):
                raise ValueError(f"source-git input is not fetchable: {item['name']}")
            if not COMMIT.fullmatch(item.get("commit", "")):
                raise ValueError(f"source-git input is not pinned: {item['name']}")
            if item["redistribution"] != "source-git-pinned":
                raise ValueError(f"source-git input has invalid redistribution: {item['name']}")
        if item["name"].startswith(SENDSPIN_PREFIX):
            # Sendspin is staged from immutable, hash-verified archives, not from
            # moving git refs: every sendspin source (and each of its transitive
            # submodules) must declare a 40-hex commit, a 64-hex archive digest,
            # and an archive URL that embeds that exact commit. A missing digest,
            # an unpinned transitive, or a tag/commit identity mismatch fails
            # closed here so it can never reach a runtime download.
            if item["kind"] != "source-git" or item["redistribution"] != "source-git-pinned":
                raise ValueError(f"sendspin input must be a pinned git source: {item['name']}")
            if not COMMIT.fullmatch(item.get("commit", "")):
                raise ValueError(f"sendspin input is not pinned to a commit: {item['name']}")
            if not str(item.get("archive_url", "")).startswith("https://") or not SHA.fullmatch(
                item.get("archive_sha256", "")
            ):
                raise ValueError(f"sendspin input must pin a hashed archive: {item['name']}")
            if not item["archive_url"].endswith(item["commit"] + ".tar.gz"):
                raise ValueError(f"sendspin input archive does not embed its commit: {item['name']}")
            seen_paths: set = set()
            for sub in _submodules(item):
                if not isinstance(sub, dict):
                    raise ValueError(f"sendspin submodule is malformed: {item['name']}")
                validate_submodule_path(item["name"], sub.get("path"), seen_paths)
                if (
                    not str(sub.get("url", "")).startswith("https://")
                    or not COMMIT.fullmatch(sub.get("commit", ""))
                    or not SHA.fullmatch(sub.get("archive_sha256", ""))
                    or not str(sub.get("archive_url", "")).endswith(sub.get("commit", "") + ".tar.gz")
                ):
                    raise ValueError(f"sendspin submodule is not pinned: {item['name']}")
        if item["kind"] == "reviewed-vendored-input":
            if not item["url"].startswith(VENDORED_PREFIX):
                raise ValueError(f"reviewed-vendored input has a non-vendored url: {item['name']}")
            if not SHA.fullmatch(item["sha256"]):
                raise ValueError(f"reviewed-vendored input must pin a digest: {item['name']}")
            if item["redistribution"] != "reviewed-vendored":
                raise ValueError(f"reviewed-vendored input has invalid redistribution: {item['name']}")
        if item["redistribution"] == "cleared":
            if not item["url"].startswith("https://") or not SHA.fullmatch(item["sha256"]):
                raise ValueError(f"cleared input is not fetchable: {item['name']}")
    if data.get("patch_inventory") is not None:
        _validate_patch_inventory(data["patch_inventory"])
    return data


def _validate_patch_inventory(patch_inventory: object) -> None:
    """Validate a ``patch_inventory`` block (Product mirror or Platform lock).

    The inventory is a closed, ordered, single-apply transform: every applied
    patch is a bare ``*.patch`` filename with a 64-hex digest and a non-empty
    target. Shape violations fail closed before any patch directory or byte is
    read.
    """
    if not isinstance(patch_inventory, dict):
        raise ValueError("patch_inventory must be an object")
    applied = patch_inventory.get("applied", [])
    if not isinstance(applied, list):
        raise ValueError("patch_inventory.applied must be a list")
    if applied and patch_inventory.get("schema") != PATCH_SCHEMA:
        raise ValueError(
            f"patch_inventory.schema must be {PATCH_SCHEMA!r} when patches are applied")
    seen: set = set()
    for index, entry in enumerate(applied):
        if not isinstance(entry, dict):
            raise ValueError(f"patch_inventory.applied[{index}] is not an object")
        name = entry.get("file", "")
        if (not isinstance(name, str) or not name.endswith(".patch") or "/" in name
                or "\\" in name or name in (".", "..") or name.startswith(".")):
            raise ValueError(
                f"patch_inventory.applied[{index}].file is not a bare *.patch name: {name!r}")
        if name in seen:
            raise ValueError("patch_inventory declares the same patch file more than once")
        seen.add(name)
        if not isinstance(entry.get("sha256"), str) or not SHA.fullmatch(entry["sha256"]):
            raise ValueError(f"patch_inventory.applied[{index}].sha256 is not 64-hex")
        if not isinstance(entry.get("target"), str) or not entry["target"]:
            raise ValueError(f"patch_inventory.applied[{index}].target is empty")


def stage_vendored(record: dict, inventory_dir: Path, destination: Path) -> Path:
    """Copy a reviewed file vendored in the repository, fail closed on its digest.

    Reviewed inputs (the 2026-06-01 CA bundle and its copyright record) are
    shipped inside the product repository because their bytes are a release
    contract: the assistant feature packager rejects any other CA bundle.
    Copying a runner's live system bundle would silently drift with runner
    image updates.
    """
    relative = record["url"][len(VENDORED_PREFIX):]
    source = inventory_dir / relative
    if not source.is_file():
        raise FileNotFoundError(f"reviewed-vendored input is missing: {source}")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != record["sha256"]:
        raise ValueError(f"reviewed-vendored input digest mismatch: {record['name']}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


def _download_urllib(url: str, destination: Path, timeout: int) -> None:
    import urllib.request

    request = urllib.request.Request(url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    with urllib.request.urlopen(request, timeout=min(timeout, 120)) as response:
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def download_to(url: str, destination: Path, *, timeout: int = DOWNLOAD_TIMEOUT) -> None:
    """Download ``url`` to ``destination`` with the best available bounded tool.

    ``curl`` is preferred, ``wget`` is the fallback, and a bounded stdlib
    urllib request is the last resort -- a host without curl must still be able
    to stage. A ``file://`` URL is copied locally so cached archives can be
    consumed through the exact same verified staging path without a network.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if url.startswith("file://"):
        source = Path(url2pathname(urlparse(url).path))
        if not source.is_file():
            raise FileNotFoundError(f"cached archive is missing: {source}")
        shutil.copyfile(source, destination)
        return
    curl = shutil.which("curl")
    if curl:
        command = [
            curl, "--fail", "--location", "--ipv4", "--retry", "5",
            "--retry-all-errors", "--connect-timeout", "30", "--max-time", str(timeout),
            "--user-agent", DOWNLOAD_USER_AGENT, "--output", str(destination), url,
        ]
    else:
        wget = shutil.which("wget")
        if wget:
            command = [
                wget, "--tries=5", "--timeout=30", "--output-document", str(destination), url,
            ]
        else:
            _download_urllib(url, destination, timeout)
            return
    subprocess.run(command, check=True, timeout=timeout)


def download_verified(
    url: str,
    declared_digest: str,
    destination: Path,
    *,
    archive_dir: Path | None = None,
    commit: str | None = None,
    timeout: int = DOWNLOAD_TIMEOUT,
) -> str:
    """Materialize archive bytes and return the *observed* SHA-256.

    Bytes are written to a private temp file, hashed, and only atomically moved
    into place once the observed digest equals the declared digest. A mismatch
    (or a partial download) leaves no output behind. When ``archive_dir`` is
    given it is the exclusive source: the archive is resolved from that local
    verified cache by its pinned ``commit`` (the digest is still recomputed
    from the real bytes), and a missing or unidentifiable archive fails closed
    instead of silently falling back to the network.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(destination.name + ".part")
    staged.unlink(missing_ok=True)
    try:
        if archive_dir is not None:
            if commit is None:
                raise ValueError(
                    f"an offline archive cache is selected but no pinned commit "
                    f"identifies this archive: {url}")
            cached = find_cached_archive(archive_dir, commit)
            shutil.copyfile(cached, staged)
        else:
            download_to(url, staged, timeout=timeout)
        observed = sha256_file(staged)
        if observed != declared_digest:
            raise ValueError(
                f"source archive digest mismatch (commit {commit or url}): "
                f"observed {observed} != declared {declared_digest}"
            )
        os.replace(staged, destination)
        return observed
    finally:
        staged.unlink(missing_ok=True)


def find_cached_archive(archive_dir: Path, commit: str) -> Path:
    """Locate the single cached archive whose name embeds ``commit``."""
    matches = sorted(p for p in Path(archive_dir).rglob("*.tar.gz") if commit in p.name)
    if not matches:
        raise FileNotFoundError(f"no cached archive embeds commit {commit} under {archive_dir}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous cached archives for commit {commit}: {[p.name for p in matches]}")
    return matches[0]


def fetch(
    record: dict,
    destination: Path,
    *,
    archive_dir: Path | None = None,
    commit: str | None = None,
) -> Path:
    if record["redistribution"] != "cleared":
        raise ValueError(f"input is not cleared: {record['name']}")
    download_verified(
        record["url"], record["sha256"], destination,
        archive_dir=archive_dir, commit=commit,
    )
    return destination


def fetch_archive(
    record: dict,
    url: str,
    digest: str,
    destination: Path,
    *,
    archive_dir: Path | None = None,
    commit: str | None = None,
) -> Path:
    """Download and digest-verify an immutable archive for a pinned source.

    Sendspin sources are staged this way instead of by cloning a git ref: the
    archive bytes are hash-verified before extraction, so a moved tag or a
    rewritten remote cannot change the staged tree. ``commit`` identifies the
    archive in the local pool and defaults to the record's own pinned commit;
    submodule archives pass their own commit explicitly.

    When ``archive_dir`` is given, the archive must already be present in that
    local verified pool, identified by the pinned commit: a missing entry fails
    closed (``FileNotFoundError``) rather than falling back to the network.
    """
    return fetch(
        {
            "name": record["name"],
            "url": url,
            "sha256": digest,
            "redistribution": "cleared",
            "kind": "source-archive",
        },
        destination,
        archive_dir=archive_dir,
        commit=commit if commit is not None else record["commit"],
    )


def _flatten(root: Path) -> Path:
    children = list(root.iterdir())
    if len(children) == 1 and children[0].is_dir() and not children[0].is_symlink():
        return children[0]
    return root


def tree_digest(root: Path) -> tuple[str, int]:
    """Canonical content digest over a staged tree.

    Symlinks are recorded by target, not followed, so a tree cannot smuggle
    identity by pointing elsewhere.
    """
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f"L\0{relative}\0{os.readlink(path)}\n".encode("utf-8"))
            count += 1
        elif path.is_file():
            digest.update(f"F\0{relative}\0{oct(path.stat().st_mode & 0o777)}\0".encode("utf-8"))
            digest.update(bytes.fromhex(sha256_file(path)) + b"\n")
            count += 1
    if count == 0:
        raise ValueError(f"staged tree is empty: {root}")
    return digest.hexdigest(), count


def _trees_identical(left: Path, right: Path) -> bool:
    try:
        return tree_digest(left) == tree_digest(right)
    except ValueError:
        return False


def stage_archive_tree(
    archive_path: Path, destination: Path, *, patches: list[VerifiedPatch] | None = None
) -> tuple[str, int]:
    """Extract a verified archive as a fresh tree; never merge into a stale one.

    The archive is unpacked into a private temp directory, flattened if the
    upstream tarball nests everything under one directory, then atomically
    renamed onto ``destination``. If ``destination`` already exists and is not
    byte-identical to the freshly extracted tree the call fails closed rather
    than overlaying stale and fresh files. Declared patches are applied from their
    frozen verified bytes to the freshly extracted content *before* the
    identical-check and rename, so a re-run is idempotent and the tree is
    confined-checked before it is published.
    """
    root = destination.parent
    root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".stage-", dir=str(root)))
    try:
        with tarfile.open(archive_path) as archive:
            archive.extractall(tmp, filter="data")
        content = _flatten(tmp)
        if patches:
            apply_patches(content, patches)
            _assert_tree_confined(content)
        if destination.exists():
            if destination.is_dir() and _trees_identical(content, destination):
                return tree_digest(destination)
            if destination.is_dir() and not any(destination.iterdir()):
                # A parent archive may carry an empty placeholder for its
                # submodule path; replace that empty directory atomically.
                os.replace(str(content), str(destination))
                return tree_digest(destination)
            raise ValueError(f"refusing to merge into existing staged tree: {destination}")
        os.replace(str(content), str(destination))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return tree_digest(destination)


def _assert_no_symlink_ancestors(base: Path, relative: str) -> None:
    current = base
    for part in relative.split("/"):
        current = current / part
        if current.is_symlink():
            raise ValueError(f"refusing submodule staging through symlink: {current}")


def _ensure_within(root: Path, path: Path) -> None:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"staging path escapes the output root: {path}")


NAMES = {
    "tts-northern-upstream": "piper-en_GB-northern_english_male-medium.onnx",
    "tts-female-upstream": "piper-en_GB-southern_english_female-low.onnx",
    "tts-tokens": "tts-tokens.txt",
    "stt-encoder": "encoder-epoch-99-avg-1.int8.onnx",
    "stt-decoder": "decoder-epoch-99-avg-1.int8.onnx",
    "stt-joiner": "joiner-epoch-99-avg-1.int8.onnx",
    "stt-tokens": "tokens.txt",
    "stt-license": "README.md",
    "wakeword-alexa": "alexa_v0.1.onnx",
    "wakeword-embedding": "embedding_model.onnx",
    "wakeword-melspectrogram": "melspectrogram.onnx",
    "libsodium": "libsodium-1.0.18.tar.gz",
    "speexdsp": "speexdsp-SpeexDSP-1.2.1.tar.gz",
    "tinyalsa": "tinyalsa-e43025bbf702eb7dd8edd48c1eb50530c60f1de8.tar.gz",
    "nqptp": "nqptp-1.2.8.tar.gz",
    "shairport-sync": "shairport-sync-5.1.tar.gz",
    "ca-certificates": "ca-certificates-20260601.crt",
    "ca-certificates-notice": "ca-certificates-20260601.copyright",
    "ota-signing-pynacl-wheel": "python-wheels/PyNaCl-1.5.0-cp36-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    "ota-signing-cffi-wheel": "python-wheels/cffi-1.17.1-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    "ota-signing-pycparser-wheel": "python-wheels/pycparser-2.22-py3-none-any.whl",
    "ota-signing-requirements": "python-wheels/requirements.txt",
    "mbedtls": "mbedtls-3.6.4.tar.bz2",
    "mbedtls-build-jinja2-wheel": "python-wheels/jinja2-3.1.6-py3-none-any.whl",
    "mbedtls-build-markupsafe-wheel": "python-wheels/markupsafe-3.0.3-cp311-cp311-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
    "mbedtls-build-jsonschema-wheel": "python-wheels/jsonschema-4.25.1-py3-none-any.whl",
    "mbedtls-build-attrs-wheel": "python-wheels/attrs-26.1.0-py3-none-any.whl",
    "mbedtls-build-jsonschema-specifications-wheel": "python-wheels/jsonschema_specifications-2025.9.1-py3-none-any.whl",
    "mbedtls-build-referencing-wheel": "python-wheels/referencing-0.37.0-py3-none-any.whl",
    "mbedtls-build-rpds-py-wheel": "python-wheels/rpds_py-2026.6.3-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    "mbedtls-build-typing-extensions-wheel": "python-wheels/typing_extensions-4.16.0-py3-none-any.whl",
    "mbedtls-build-requirements": "python-wheels/mbedtls-build-requirements.txt",
    "plistutil-package": "host-packages/libplist-utils.deb",
    "libplist-runtime-package": "host-packages/libplist-runtime.deb",
    "libplist-source": "host-tools/source/libplist_2.3.0.orig.tar.bz2",
    "libplist-debian-source": "host-tools/source/libplist_2.3.0-1~exp2build2.debian.tar.xz",
    "libplist-source-descriptor": "host-tools/source/libplist_2.3.0-1~exp2build2.dsc",
}

# Product-inventory name -> (role, SOURCE.lock name) for the cross-repo lock
# reconciliation. The SOURCE.lock identity block is keyed by role; dependencies
# are keyed by upstream name.
SENDSPIN_LOCK_ROLES = {
    "protocol_spec": "sendspin-protocol-spec",
    "sdk": "sendspin-sdk",
    "server_oracle": "sendspin-server-oracle",
}
SENDSPIN_LOCK_DEPENDENCIES = {
    "ArduinoJson": "sendspin-arduinojson",
    "IXWebSocket": "sendspin-ixwebsocket",
    "micro-flac": "sendspin-micro-flac",
    "noise-c": "sendspin-noise-c",
}

# The focused production stage must materialize the complete frozen Sendspin
# closure; an empty or incomplete inventory fails closed rather than printing a
# vacuous ``sources=0``/partial PASS. Derived from the same role/dependency map
# used for SOURCE.lock reconciliation so it cannot silently drift from the
# locked closure (and tests inject a small closure instead of the real one).
SENDSPIN_REQUIRED_SOURCES = frozenset(
    set(SENDSPIN_LOCK_ROLES.values()) | set(SENDSPIN_LOCK_DEPENDENCIES.values())
)


def _product_name_for_lock_name(name: str) -> str | None:
    """Map a SOURCE.lock patch target name to its Product inventory source name."""
    if name.startswith("identity::"):
        return SENDSPIN_LOCK_ROLES.get(name.split("::", 1)[1])
    return SENDSPIN_LOCK_DEPENDENCIES.get(name)


def _patch_applied_tuples(patch_inventory: object) -> list[tuple]:
    """Canonical ``(file, sha256, target, pristine anchor)`` tuples in declared order.

    The four fields are the cross-repo contract: the lock, the Product mirror and
    the architecture document must agree on each patch's file, digest, target and
    pristine-archive anchor, in the same order.
    """
    if patch_inventory is None:
        return []
    if not isinstance(patch_inventory, dict):
        raise ValueError("patch_inventory must be an object")
    applied = patch_inventory.get("applied", [])
    if not isinstance(applied, list):
        raise ValueError("patch_inventory.applied must be a list")
    tuples: list[tuple] = []
    for index, entry in enumerate(applied):
        if not isinstance(entry, dict):
            raise ValueError(f"patch_inventory.applied[{index}] is not an object")
        tuples.append((
            entry.get("file"),
            entry.get("sha256"),
            entry.get("target"),
            entry.get("pristine_archive_sha256"),
        ))
    return tuples


def reconcile_source_lock(inventory: dict, lock: dict) -> dict:
    """Require the Platform SOURCE.lock to agree with the Product inventory.

    Compares the full identity and dependency fields (repository, commit,
    archive_url, archive_sha256, license) and every submodule field. Raises
    ``ValueError`` listing every mismatch, so a SOURCE.lock-only (or
    inventory-only) drift cannot pass.
    """
    records = {
        item["name"]: item
        for item in inventory.get("inputs", [])
        if isinstance(item, dict) and item.get("name", "").startswith(SENDSPIN_PREFIX)
    }
    identity = lock.get("identity", {})
    dependencies = {
        entry["name"]: entry
        for entry in lock.get("dependencies", [])
        if isinstance(entry, dict) and entry.get("name")
    }
    problems: list[str] = []

    def compare(label: str, lock_entry: dict, record: dict, *, submodule: bool = False) -> None:
        for lock_key, record_key in (
            ("commit", "commit"),
            ("archive_sha256", "archive_sha256"),
            ("archive_url", "archive_url"),
        ):
            if lock_entry.get(lock_key) != record.get(record_key):
                problems.append(
                    f"{label}.{lock_key}={lock_entry.get(lock_key)!r} != inventory {record.get(record_key)!r}"
                )
        if not submodule:
            if lock_entry.get("repository") != record.get("url"):
                problems.append(
                    f"{label}.repository={lock_entry.get('repository')!r} != inventory {record.get('url')!r}"
                )
            if lock_entry.get("license") != record.get("license"):
                problems.append(
                    f"{label}.license={lock_entry.get('license')!r} != inventory {record.get('license')!r}"
                )

    for role, name in SENDSPIN_LOCK_ROLES.items():
        record = records.get(name)
        entry = identity.get(role)
        if record is None:
            problems.append(f"inventory is missing {name}")
            continue
        if not isinstance(entry, dict):
            problems.append(f"SOURCE.lock identity.{role} is missing")
            continue
        compare(f"identity.{role}", entry, record)

    for lock_name, name in SENDSPIN_LOCK_DEPENDENCIES.items():
        record = records.get(name)
        entry = dependencies.get(lock_name)
        if record is None:
            problems.append(f"inventory is missing {name}")
            continue
        if not isinstance(entry, dict):
            problems.append(f"SOURCE.lock dependency {lock_name} is missing")
            continue
        compare(lock_name, entry, record)
        lock_subs = {
            sub["path"]: sub
            for sub in entry.get("submodules", [])
            if isinstance(sub, dict) and isinstance(sub.get("path"), str)
        }
        record_subs = {
            sub["path"]: sub
            for sub in record.get("submodules", [])
            if isinstance(sub, dict) and isinstance(sub.get("path"), str)
        }
        if set(lock_subs) != set(record_subs):
            problems.append(
                f"{lock_name}: submodule paths {sorted(lock_subs)} != inventory {sorted(record_subs)}"
            )
        for path in sorted(set(lock_subs) & set(record_subs)):
            compare(f"{lock_name}::{path}", lock_subs[path], record_subs[path], submodule=True)

    extra = {
        name
        for role, name in {**SENDSPIN_LOCK_ROLES, **SENDSPIN_LOCK_DEPENDENCIES}.items()
        if name not in records
    }
    if extra:
        problems.append(f"inventory sendspin closure mismatch: extra {sorted(extra)}")

    # The patch transform is part of the same cross-repo contract: the Product
    # mirror and the Platform lock must declare the same patches (file, digest,
    # target, pristine anchor) in the same order, and the bytes stay
    # Platform-owned. A lock-only or mirror-only patch cannot pass.
    lock_applied = _patch_applied_tuples(lock.get("patch_inventory"))
    mirror_applied = _patch_applied_tuples(inventory.get("patch_inventory"))
    if lock_applied != mirror_applied:
        problems.append(
            f"patch_inventory applied {lock_applied} != Product inventory mirror {mirror_applied}")
    if lock_applied:
        lock_pi = lock.get("patch_inventory") or {}
        if lock_pi.get("schema") != PATCH_SCHEMA:
            problems.append(f"SOURCE.lock patch_inventory.schema must be {PATCH_SCHEMA!r}")
        mirror = inventory.get("patch_inventory") or {}
        if mirror.get("owner") != SENDSPIN_PATCH_OWNER:
            problems.append(
                f"inventory patch_inventory.owner {mirror.get('owner')!r} != "
                f"{SENDSPIN_PATCH_OWNER!r}")

    if problems:
        raise ValueError("SOURCE.lock does not match the Product inventory: " + "; ".join(problems))
    return lock


def _safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", key)


def _extract_verified_archive(
    output: Path,
    key: str,
    url: str,
    declared_digest: str,
    commit: str,
    destination: Path,
    *,
    archive_dir: Path | None = None,
) -> str:
    """Download+verify an archive, then extract it flattened onto ``destination``.

    The archive is hashed before anything is unpacked and extracted into a
    private temp directory that is flattened (when the tarball nests everything
    under one top-level directory) and atomically renamed onto ``destination``,
    which must not already exist. Returns the *observed* archive digest.
    """
    archive = output / f".{_safe_key(key)}-{commit}.archive"
    tmp = Path(tempfile.mkdtemp(prefix=f".extract-{_safe_key(key)}-", dir=str(output)))
    try:
        observed = download_verified(
            url, declared_digest, archive, archive_dir=archive_dir, commit=commit
        )
        with tarfile.open(archive) as handle:
            handle.extractall(tmp, filter="data")
        content = _flatten(tmp)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(content), str(destination))
        return observed
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _publish_staged_tree(content: Path, destination: Path) -> None:
    """Atomically move an assembled tree onto ``destination``, else fail closed.

    A missing destination is populated by a single rename. An existing
    destination is accepted only when it is byte-identical to ``content`` (a
    re-run over identical inputs) or an empty placeholder directory; any other
    existing content is refused rather than overlaid with stale/fresh files.
    """
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError(f"refusing to replace non-directory staged path: {destination}")
        if _trees_identical(content, destination):
            return
        if not any(destination.iterdir()):
            os.replace(str(content), str(destination))
            return
        raise ValueError(f"refusing to merge into existing staged tree: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(content), str(destination))


def _patch_targets(data: bytes) -> list[str]:
    """Return the file paths a unified diff addresses (its ---/+++ header lines)."""
    targets: list[str] = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            targets.append(line[4:].split("\t", 1)[0].strip())
    return targets


def _assert_patch_targets_safe(name: str, data: bytes) -> None:
    """Refuse a diff that addresses an absolute or traversing path.

    ``patch -p1`` strips the ``a/``/``b/`` prefix, but a hostile header could still
    name ``/etc/...`` or ``../../...``; the transform must stay inside the tree.
    """
    for target in _patch_targets(data):
        if target == "/dev/null":
            continue
        if target.startswith("/") or ".." in PurePosixPath(target).parts:
            raise ValueError(f"patch {name} targets unsafe path {target!r}")


def _run_patch(tree: Path, data: bytes, dry_run: bool):
    # The verified bytes are fed on stdin, so no patch file is written to — or
    # later re-read from — any mutable path: the applied transform is exactly the
    # buffer that was hashed. ``-p1`` matches the Platform verifier's transform,
    # so the Product and Platform results are byte-identical.
    command = ["patch", "-p1", "--no-backup-if-mismatch"]
    if dry_run:
        command.append("--dry-run")
    command += ["-d", str(tree)]
    return subprocess.run(command, input=data, capture_output=True)


def _patch_output(completed) -> str:
    return (
        (completed.stdout or b"").decode("utf-8", "replace")
        + (completed.stderr or b"").decode("utf-8", "replace")
    )


def apply_patches(tree: Path, patches: list[VerifiedPatch]) -> None:
    """Apply each verified patch, in order, from its frozen bytes.

    Each patch is applied from the digest-verified buffer it was read into (never
    a re-opened path), must apply cleanly exactly once, and must not apply a
    second time — a diff that applies twice is not a deterministic single-apply
    transform.
    """
    tree = Path(tree)
    for patch in patches:
        _assert_patch_targets_safe(patch.file, patch.data)
        dry = _run_patch(tree, patch.data, dry_run=True)
        if dry.returncode != 0:
            raise ValueError(
                f"patch {patch.file} does not apply to the pinned tree: {_patch_output(dry)}")
        real = _run_patch(tree, patch.data, dry_run=False)
        if real.returncode != 0:
            raise ValueError(
                f"patch {patch.file} failed to apply: {_patch_output(real)}")
        again = _run_patch(tree, patch.data, dry_run=True)
        if again.returncode == 0:
            raise ValueError(
                f"patch {patch.file} applies a second time; the inventory is not a "
                f"single-apply transform")


def _assert_tree_confined(root: Path) -> None:
    """Refuse a staged tree whose entries escape it or are non-regular.

    A digest-pinned patch can still create a *symlink whose body points outside
    the tree* (a git ``new file mode 120000`` diff) even when its header path is
    safe; ``tree_digest`` records such a link by target rather than following it,
    so the escape is latent until a consumer opens it. Every entry is inspected
    before publication: no symlinked directory, no symlink whose target is
    absolute or climbs above the tree, no setuid/setgid file, and no
    fifo/socket/device entry. The downstream Platform ``--compare-tree`` is left
    unchanged; this is an additional pre-publication gate.
    """
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        for name in dirnames:
            if (here / name).is_symlink():
                raise ValueError(
                    f"staged tree contains a symlinked directory: {here / name}")
        for name in filenames:
            entry = here / name
            if entry.is_symlink():
                target = os.readlink(entry)
                if os.path.isabs(target):
                    raise ValueError(
                        f"staged tree contains a symlink to an absolute path: "
                        f"{entry} -> {target}")
                parent = entry.parent.relative_to(root).as_posix()
                normalized = posixpath.normpath(
                    posixpath.join("" if parent == "." else parent, target))
                if normalized == ".." or normalized.startswith("../"):
                    raise ValueError(
                        f"staged tree contains an escaping symlink: {entry} -> {target}")
                continue
            mode = entry.stat(follow_symlinks=False).st_mode
            if not stat.S_ISREG(mode):
                raise ValueError(f"staged tree contains a non-regular entry: {entry}")
            if mode & (stat.S_ISUID | stat.S_ISGID):
                raise ValueError(
                    f"staged tree contains a setuid/setgid entry: {entry}")


def resolve_patch_map(
    inventory: dict,
    records: list[dict],
    patch_dir: Path | None,
    *,
    lock: dict | None = None,
) -> dict[str, list[VerifiedPatch]]:
    """Authenticate the declared patch mirror and the patch bytes/paths.

    Returns ``{product_source_name: [VerifiedPatch, ...]}`` in declared order, or
    an empty map when the inventory declares no patches. Every gate runs *before*
    anything is materialized, and each patch's bytes are read exactly once into a
    frozen buffer:

    * the mirror shape is validated (bare ``*.patch`` name, 64-hex digest, target);
    * when a Platform ``lock`` is supplied its ``patch_inventory`` must equal the
      Product mirror (same files, digests, targets, anchors, in the same order);
    * a declared mirror requires the exact Platform ``owner`` **and** an explicit
      ``lock`` and ``patch_dir`` (there is no implicit default, no arbitrary
      checkout, and no owner==None back-compat bypass);
    * the directory is enumerated closed: every entry — including dotfiles,
      subdirectories and symlinks — must be one of the declared bare ``*.patch``
      regular files (no missing, extra, path-component or link entries);
    * every declared patch's bytes are read once through a no-follow descriptor and
      hashed to its declared digest; the verified bytes are what gets applied;
    * each patch's target maps to a staged sendspin source and its pristine anchor
      matches that source's pinned archive digest;
    * every diff header stays inside the target tree.
    """
    mirror = inventory.get("patch_inventory")
    if mirror is not None:
        _validate_patch_inventory(mirror)
    applied = (mirror or {}).get("applied", []) if isinstance(mirror, dict) else []

    if lock is not None:
        lock_applied = _patch_applied_tuples(lock.get("patch_inventory"))
        if lock_applied != _patch_applied_tuples(mirror):
            raise ValueError(
                "SOURCE.lock patch_inventory does not match the Product inventory mirror: "
                f"lock={lock_applied} mirror={_patch_applied_tuples(mirror)}")
    if not applied:
        return {}
    mirror = mirror or {}
    # Ownership is a hard gate, independent of whether a lock was supplied: the
    # mirror may only ever name the Platform owner — a missing/None owner is a
    # bypass, not back-compat.
    if mirror.get("owner") != SENDSPIN_PATCH_OWNER:
        raise ValueError(
            f"patch mirror owner {mirror.get('owner')!r} is not {SENDSPIN_PATCH_OWNER!r}")
    # A declared patch is never applied from an unauthenticated transform: the
    # Platform SOURCE.lock is required so the mirror can be reconciled, even when
    # the caller would otherwise be happy to skip it.
    if lock is None:
        raise ValueError(
            "the inventory declares applied patches; the Platform SOURCE.lock (lock) is "
            "required to authenticate them before staging")
    if patch_dir is None:
        raise ValueError(
            "the inventory declares applied patches; an explicit Platform-owned patch "
            "directory (patch_dir) is required")
    patch_dir = Path(patch_dir)
    if not patch_dir.is_dir():
        raise ValueError(f"patch directory is missing: {patch_dir}")

    declared = [entry["file"] for entry in applied]
    # Closed enumeration: *every* entry in the directory — not just the ones a
    # ``glob("*.patch")`` would match — must be a declared bare *.patch regular
    # file. A ".hidden.patch", a "sub/x.patch" or a symlink is refused, so the
    # directory contract cannot be quietly widened.
    try:
        scanned = list(os.scandir(patch_dir))
    except OSError as exc:
        raise ValueError(f"cannot enumerate patch directory {patch_dir}: {exc}") from exc
    on_disk: set = set()
    for entry in scanned:
        if (entry.name.startswith(".") or entry.is_symlink()
                or not entry.is_file(follow_symlinks=False)):
            raise ValueError(
                "patch directory holds an unexpected entry (only declared bare *.patch "
                f"regular files are allowed): {entry.name!r}")
        on_disk.add(entry.name)
    missing = sorted(set(declared) - on_disk)
    extra = sorted(on_disk - set(declared))
    if missing or extra:
        raise ValueError(f"patch inventory mismatch: missing={missing} extra={extra}")

    by_name = {record["name"]: record for record in records}
    patch_map: dict[str, list[VerifiedPatch]] = {}
    for entry in applied:
        path = patch_dir / entry["file"]
        data = _read_regular_file_nofollow(path)
        actual = hashlib.sha256(data).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError(
                f"patch {entry['file']}: sha256 {actual} != declared {entry['sha256']}")
        product_name = _product_name_for_lock_name(entry["target"])
        if product_name is None or product_name not in by_name:
            raise ValueError(
                f"patch {entry['file']} targets {entry['target']!r}, which is not a staged "
                f"sendspin source")
        anchor = entry.get("pristine_archive_sha256")
        pinned = by_name[product_name].get("archive_sha256")
        if anchor is not None and anchor != pinned:
            raise ValueError(
                f"patch {entry['file']} is anchored to {anchor}, not {product_name}'s "
                f"pinned archive {pinned}")
        _assert_patch_targets_safe(entry["file"], data)
        patch_map.setdefault(product_name, []).append(
            VerifiedPatch(entry["file"], actual, data))
    return patch_map


def _stage_sendspin_source(
    output: Path,
    record: dict,
    subs: list[tuple[str, dict]],
    *,
    archive_dir: Path | None = None,
    patches: list[VerifiedPatch] | None = None,
) -> dict:
    """Assemble one source and its declared submodules, then publish atomically.

    The whole component (parent tree plus every declared submodule subtree) is
    built inside a private temp tree first. It is only compared against and
    published onto the destination once fully assembled, so a failed submodule
    download can never leave a partial parent behind, and a re-run over
    identical content is idempotent. Declared patches are applied, in order and
    exactly once, to the *freshly extracted* verified-archive tree before any
    submodule is staged and before the receipt digests are computed, so the
    receipt content hashes always describe the final patched on-disk tree. Every
    entry's ``tree_sha256`` and ``file_count`` are recomputed from the *final*
    on-disk tree (the source digest covers its declared submodule subtrees in
    full). A parent archive that ships content at a declared submodule path --
    anything other than an empty placeholder directory -- is refused, never
    silently overlaid.
    """
    name = record["name"]
    patches = list(patches or [])
    build = Path(tempfile.mkdtemp(prefix=f".build-{_safe_key(name)}-", dir=str(output)))
    entries: dict[str, dict] = {}
    try:
        root = build / "root"
        parent_observed = _extract_verified_archive(
            output, name, record["archive_url"], record["archive_sha256"],
            record["commit"], root, archive_dir=archive_dir,
        )
        if patches:
            apply_patches(root, patches)
        sub_meta: dict[str, dict] = {}
        for path, sub in subs:
            _assert_no_symlink_ancestors(root, path)
            destination = root / path
            if destination.is_symlink():
                raise ValueError(
                    f"parent ships a symlink at the declared submodule path: {name}:{path}"
                )
            if destination.exists():
                if not destination.is_dir():
                    raise ValueError(
                        f"parent ships a file at the declared submodule path: {name}:{path}"
                    )
                if any(destination.iterdir()):
                    raise ValueError(
                        "parent ships content at the declared submodule path "
                        f"(divergent parent/child): {name}:{path}"
                    )
                destination.rmdir()
            observed = _extract_verified_archive(
                output, f"{name}::{path}", sub["archive_url"], sub["archive_sha256"],
                sub["commit"], destination, archive_dir=archive_dir,
            )
            sub_meta[path] = {"sub": sub, "observed": observed}

        # Inspect the whole assembled tree (parent + every submodule) before it is
        # published: a digest-pinned patch can smuggle an escaping symlink or a
        # special mode even though its header paths are safe.
        _assert_tree_confined(root)
        final = output / name
        _publish_staged_tree(root, final)

        tree, count = tree_digest(final)
        entries[name] = {
            "commit": record["commit"],
            "archive_url": record["archive_url"],
            "archive_sha256_declared": record["archive_sha256"],
            "archive_sha256_observed": parent_observed,
            "archive_verified": parent_observed == record["archive_sha256"],
            "tree_sha256": tree,
            "file_count": count,
            "staged_path": final.relative_to(output).as_posix(),
            "patches": [
                {"file": patch.file, "sha256": patch.sha256} for patch in patches
            ],
        }
        for path, meta in sub_meta.items():
            sub_tree, sub_count = tree_digest(final / path)
            entries[f"{name}::{path}"] = {
                "commit": meta["sub"]["commit"],
                "archive_url": meta["sub"]["archive_url"],
                "archive_sha256_declared": meta["sub"]["archive_sha256"],
                "archive_sha256_observed": meta["observed"],
                "archive_verified": meta["observed"] == meta["sub"]["archive_sha256"],
                "tree_sha256": sub_tree,
                "file_count": sub_count,
                "staged_path": (final / path).relative_to(output).as_posix(),
            }
        return entries
    finally:
        shutil.rmtree(build, ignore_errors=True)


def stage_sendspin(
    inventory: dict,
    output: Path,
    *,
    archive_dir: Path | None = None,
    receipt_path: Path | None = None,
    required_closure: Iterable[str] | None = None,
    patch_dir: Path | None = None,
    lock: dict | None = None,
) -> dict:
    """Stage only the pinned Sendspin sources and write a content receipt.

    This is the focused path: it never touches unrelated audio/model inputs or
    the libplist host tools. Every path is validated before anything is fetched
    or extracted, every archive is digest-verified before extraction, and each
    component (source plus declared submodules) is assembled in a private temp
    tree and published atomically, or the staging fails closed.

    When the inventory declares an applied ``patch_inventory`` mirror, the
    authenticated Platform-owned patch bytes are applied to the freshly
    archive-derived tree from the *explicit* ``patch_dir`` (there is no implicit
    default), after the lock/mirror and patch bytes/paths are all validated and
    before anything is materialized. ``lock`` (a Platform SOURCE.lock) is
    **required** whenever patches are declared — the exact Platform ``owner`` and
    the lock/mirror agreement are both enforced — and its bytes are read once into
    a frozen, digest-verified buffer that is what actually gets applied. The
    receipt's content digests are computed after the patch and every submodule
    land, so they always describe the final on-disk tree, and the whole staged
    tree is confined-checked before it is published.

    A zero-source closure always fails closed. When ``required_closure`` is
    given the inventory's sendspin *source* names must equal it exactly, so an
    empty or incomplete inventory cannot produce a vacuous PASS. The production
    CLI passes ``SENDSPIN_REQUIRED_SOURCES``; tests inject a small closure. The
    receipt records the declared digest, the *observed* digest, the staged tree
    content digest for every source and submodule, and the applied patches.
    """
    records = [
        record
        for record in inventory.get("inputs", [])
        if isinstance(record, dict) and record.get("name", "").startswith(SENDSPIN_PREFIX)
    ]
    if not records:
        raise ValueError("sendspin staging closure is empty")
    if required_closure is not None:
        required = set(required_closure)
        present = {record["name"] for record in records}
        if present != required:
            raise ValueError(
                "sendspin staging closure does not match the required closure: "
                f"missing={sorted(required - present)} extra={sorted(present - required)}"
            )

    plans: list[tuple[dict, list[tuple[str, dict]]]] = []
    for record in records:
        seen: set = set()
        subs = []
        for sub in _submodules(record):
            path = validate_submodule_path(record["name"], sub.get("path"), seen)
            subs.append((path, sub))
        plans.append((record, subs))

    # Authenticate the patch mirror, the lock/mirror agreement and the patch
    # bytes/paths before creating any output path.
    patch_map = resolve_patch_map(inventory, records, patch_dir, lock=lock)

    output.mkdir(parents=True, exist_ok=True)
    entries: dict[str, dict] = {}
    for record, subs in plans:
        component_dir = output / record["name"]
        _ensure_within(output, component_dir)
        entries.update(_stage_sendspin_source(
            output, record, subs, archive_dir=archive_dir,
            patches=patch_map.get(record["name"], []),
        ))

    expected = {record["name"] for record, _ in plans}
    expected |= {f"{record['name']}::{path}" for record, subs in plans for path, _ in subs}
    applied_patches = [
        {"file": patch.file, "sha256": patch.sha256, "target": name}
        for name in sorted(patch_map) for patch in patch_map[name]
    ]
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "feature": "sendspin",
        "tree_digest_scope": "full-tree-including-declared-submodules",
        "patch_inventory_owner": SENDSPIN_PATCH_OWNER if applied_patches else None,
        "patches_applied": applied_patches,
        "complete": set(entries) == expected,
        "entries": entries,
    }
    if not receipt["complete"]:
        raise ValueError("sendspin staging receipt is incomplete")
    target = Path(receipt_path) if receipt_path else output / "sendspin-stage-receipt.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    # The receipt is always recomputed from the final patched on-disk tree; a
    # stale or forged receipt left at the target path is overwritten, never trusted.
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt["receipt_path"] = str(target)
    return receipt


def _stage_libplist_host_tools(output: Path) -> None:
    """Package the libplist host tools and write the inventory ``SHA256SUMS``."""
    packages = output / "host-packages"
    for package in (packages / "libplist-utils.deb", packages / "libplist-runtime.deb"):
        target = output / "host-tools"
        target.mkdir(exist_ok=True)
        subprocess.run(["dpkg-deb", "-x", str(package), str(target)], check=True)
    (output / "host-tools/bin").mkdir(parents=True, exist_ok=True)
    (output / "host-tools/lib").mkdir(parents=True, exist_ok=True)
    subprocess.run(["cp", str(output / "host-tools/usr/bin/plistutil"), str(output / "host-tools/bin/plistutil")], check=True)
    runtime = next((output / "host-tools/usr/lib").rglob("libplist-2.0.so.4*"))
    subprocess.run(["cp", str(runtime), str(output / "host-tools/lib/libplist-2.0.so.4")], check=True)
    source_root = output / "host-tools/source"
    (output / "host-tools/share/libplist").mkdir(parents=True, exist_ok=True)
    copyright_file = next((output / "host-tools/usr/share/doc").rglob("copyright"))
    subprocess.run(["cp", str(copyright_file), str(output / "host-tools/share/libplist/copyright")], check=True)
    with tarfile.open(source_root / "libplist_2.3.0.orig.tar.bz2") as archive:
        member = next(x for x in archive.getmembers() if x.name.endswith("/COPYING.LESSER"))
        target = output / "host-tools/share/libplist/COPYING.LESSER"
        target.write_bytes(archive.extractfile(member).read())
    manifest = {
        "schema": 1, "architecture": "amd64", "source_package": "libplist",
        "package_version": "2.3.0-1~exp2build2", "plistutil_package": "libplist-utils",
        "plistutil_package_sha256": "8a5c32845d9a33a052ff82412d77a2831f3f77672024610044ee8aa06d3604fa",
        "plistutil_sha256": hashlib.sha256((output / "host-tools/bin/plistutil").read_bytes()).hexdigest(),
        "runtime_package": "libplist-2.0-4", "runtime_package_sha256": "e425c79a3e6e336ce05be7ad7d4171d0a956437cd69d13f27e0df98e272a6f26",
        "libplist_sha256": hashlib.sha256((output / "host-tools/lib/libplist-2.0.so.4").read_bytes()).hexdigest(),
        "license": "LGPL-2.1-or-later", "source": "https://archive.ubuntu.com/ubuntu/pool/main/libp/libplist/", "ubuntu_suite": "noble"
    }
    (output / "host-tools/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    sums = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            sums.append(f"{digest}  {path.relative_to(output)}")
    (output / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")


def stage(
    inventory: dict,
    output: Path,
    inventory_dir: Path,
    *,
    patch_dir: Path | None = None,
    lock: dict | None = None,
    archive_dir: Path | None = None,
) -> None:
    """Materialize the whole inventory, fail-closed on a declared patch transform.

    The whole-inventory route (used by the release workflow) authenticates a
    declared ``patch_inventory`` exactly as the focused stage does: the exact
    Platform ``owner``, the Platform SOURCE.lock, and an explicit ``patch_dir``
    are all required, and every declared patch is applied from its frozen
    verified bytes to the freshly archive-derived tree. Without those inputs the
    route refuses *before* creating the output path, so it can never materialize
    a pristine tree under an inventory that declares a patch applied.

    ``archive_dir`` is threaded into every commit-pinned archive fetch: when it
    is given, each such archive is resolved from that local verified pool and a
    missing entry fails closed instead of reaching the network.
    """
    # Authenticate any declared patch transform before creating the output path.
    patch_map = resolve_patch_map(inventory, inventory["inputs"], patch_dir, lock=lock)
    output.mkdir(parents=True, exist_ok=True)
    applied_targets: set = set()
    for record in inventory["inputs"]:
        if record["kind"] == "reviewed-vendored-input":
            relative = NAMES[record["name"]]
            stage_vendored(record, inventory_dir, output / relative)
            continue
        if record["kind"] == "source-git":
            if record.get("archive_sha256"):
                # Immutable, digest-verified staging (no git ref is followed).
                destination = output / record["name"]
                archive_path = output / (record["name"] + ".archive")
                fetch_archive(
                    record, record["archive_url"], record["archive_sha256"], archive_path,
                    archive_dir=archive_dir,
                )
                stage_archive_tree(
                    archive_path, destination, patches=patch_map.get(record["name"]))
                applied_targets.add(record["name"])
                seen: set = set()
                for sub in _submodules(record):
                    path = validate_submodule_path(record["name"], sub["path"], seen)
                    _assert_no_symlink_ancestors(destination, path)
                    sub_destination = destination / path
                    _ensure_within(output, sub_destination)
                    sub_archive = output / (record["name"] + ".submodule.archive")
                    fetch_archive(
                        record, sub["archive_url"], sub["archive_sha256"], sub_archive,
                        archive_dir=archive_dir, commit=sub["commit"],
                    )
                    stage_archive_tree(sub_archive, sub_destination)
                continue
            checkout = output / record["name"]
            subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", record["url"], str(checkout)], check=True)
            subprocess.run(["git", "-C", str(checkout), "checkout", "--quiet", record["commit"]], check=True)
            continue
        if record["kind"] == "source-archive-tree":
            archive_path = output / (record["name"] + ".archive")
            fetch(record, archive_path)
            stage_archive_tree(archive_path, output / record["name"])
            continue
        if record["redistribution"] != "cleared":
            continue
        relative = NAMES.get(record["name"], record["url"].rsplit("/", 1)[-1])
        fetch(record, output / relative)
    # Every declared patch target must have been materialized as a patched archive
    # tree; otherwise the transform was silently dropped.
    unapplied = sorted(set(patch_map) - applied_targets)
    if unapplied:
        raise ValueError(
            "declared patches were not applied (target not staged as an archive tree): "
            f"{unapplied}")
    # The libplist host tools are packaged only when the inventory declares them;
    # an inventory without them must not require dpkg-deb or pre-staged .debs.
    if any(record.get("name") == "plistutil-package" for record in inventory["inputs"]):
        _stage_libplist_host_tools(output)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-generated", action="store_true")
    parser.add_argument("--feature", choices=["sendspin"],
                        help="stage only the named feature's pinned sources")
    parser.add_argument("--archive-dir", type=Path,
                        help="verified local archive cache; avoids the network")
    parser.add_argument("--receipt", type=Path, help="write a stage receipt JSON")
    parser.add_argument("--patch-dir", type=Path,
                        help="the Platform-owned patches directory declared by the inventory "
                             "patch_inventory; required (with --source-lock) when the inventory "
                             "declares applied patches (there is no implicit default)")
    parser.add_argument("--source-lock", type=Path,
                        help="the Platform SOURCE.lock that authenticates the patch mirror; "
                             "required when the inventory declares applied patches")
    args = parser.parse_args(argv)
    data = load(args.inventory)
    # A focused feature stage only concerns that feature's records, so unrelated
    # blocked inputs cannot veto it; the whole-inventory stage still checks all.
    targets = (
        [x for x in data["inputs"] if x["name"].startswith(SENDSPIN_PREFIX)]
        if args.feature
        else data["inputs"]
    )
    blocked = [x["name"] for x in targets if x["redistribution"].startswith(("blocked-private", "requires-"))]
    if not args.allow_generated:
        blocked += [x["name"] for x in targets if x["redistribution"].startswith("blocked-generation")]
    if blocked:
        raise SystemExit("PUBLIC_INPUTS_BLOCKED: " + ",".join(blocked))
    # The Platform SOURCE.lock is loaded once and reconciled for *every* route:
    # a whole-inventory run that declares a patch transform must authenticate it
    # exactly like the focused stage (no route gets an implicit fallback).
    lock = None
    if args.source_lock is not None:
        lock = json.loads(Path(args.source_lock).read_text(encoding="utf-8"))
        reconcile_source_lock(data, lock)
    if args.feature:
        if not args.output:
            parser.error("--feature requires --output")
        receipt = stage_sendspin(
            data, args.output, archive_dir=args.archive_dir,
            receipt_path=args.receipt, required_closure=SENDSPIN_REQUIRED_SOURCES,
            patch_dir=args.patch_dir, lock=lock,
        )
        patches = len(receipt.get("patches_applied", []))
        print(f"public_inputs=PASS feature=sendspin sources={len(receipt['entries'])} "
              f"patches={patches} receipt={receipt['receipt_path']}")
    elif args.output:
        stage(data, args.output, args.inventory.parent,
              patch_dir=args.patch_dir, lock=lock, archive_dir=args.archive_dir)
        print(f"public_inputs=PASS count={len(data['inputs'])}")
    else:
        print(f"public_inputs=PASS count={len(data['inputs'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
