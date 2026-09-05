#!/usr/bin/env python3
"""Content-addressed, fail-closed component cache for iteration builds."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
import time
from typing import NoReturn

SCHEMA = 5
COMPONENTS_SCHEMA = "libreecho-components-v1"
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_KEY = re.compile(r"^[0-9a-f]{64}$")
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


def _die(message: str) -> NoReturn:
    raise SystemExit(f"ERROR: {message}")


def _safe_component(component: str) -> None:
    if not _COMPONENT.fullmatch(component):
        _die(f"malformed component name: {component!r}")


def _safe_key(key: str) -> None:
    if not _KEY.fullmatch(key):
        _die(f"malformed cache key: {key!r}")


def _safe_manifest_relative(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        value not in {"", "."}
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.as_posix() == value
    )


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _parse_inputs(raw_values: list[str], kind: str) -> list[tuple[str, Path]]:
    parsed: list[tuple[str, Path]] = []
    labels: set[str] = set()
    for raw in raw_values:
        if raw.count("=") != 1:
            _die(f"{kind} input must be logical=path: {raw!r}")
        label, value = raw.split("=", 1)
        if not _LABEL.fullmatch(label) or not value:
            _die(f"malformed {kind} logical label: {raw!r}")
        if label in labels:
            _die(f"duplicate logical input label: {label}")
        labels.add(label)
        parsed.append((label, Path(value)))
    return parsed


def _digest_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    while True:
        block = os.read(descriptor, 1024 * 1024)
        if not block:
            return digest.hexdigest()
        digest.update(block)


def _file_digest(path: Path) -> str:
    try:
        descriptor = os.open(path, _FILE_FLAGS)
    except OSError as exc:
        _die(f"cannot read input {path}: {exc}")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            _die(f"input is not a regular file: {path}")
        return _digest_fd(descriptor)
    finally:
        os.close(descriptor)


def _symlink_target_identity(link: Path, resolved: Path, anchor: Path) -> str:
    """Hash logical link syntax without embedding absolute host roots."""
    try:
        anchor_resolved = anchor.resolve(strict=True)
        relative = os.path.relpath(resolved, anchor_resolved)
    except (OSError, ValueError) as exc:
        _die(f"cannot canonicalize cache key symlink target {link}: {exc}")
    raw_target = os.readlink(link)
    if os.path.isabs(raw_target):
        # The resolved target content and mode are hashed by the caller. Keep
        # absolute syntax and the leaf identity, but never make the key depend
        # on checkout depth or an absolute host prefix.
        raw_logical = f"<absolute>/{PurePosixPath(raw_target).name}"
        resolved_logical = "<content-addressed>"
    else:
        raw_logical = raw_target
        resolved_logical = relative
    return hashlib.sha256(
        b"symlink-target-v3\0raw-logical=" + os.fsencode(raw_logical)
        + b"\0resolved-logical=" + os.fsencode(resolved_logical)
    ).hexdigest()


def _key_file_digest(path: Path) -> str:
    try:
        info = path.lstat()
    except OSError as exc:
        _die(f"cache key file is missing or unsafe: {path}: {exc}")
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        try:
            resolved = path.resolve(strict=True)
            resolved_info = resolved.stat()
        except OSError as exc:
            _die(f"cache key file symlink target is unsafe: {path}: {exc}")
        if not stat.S_ISREG(resolved_info.st_mode):
            _die(f"cache key file symlink target is not regular: {path}")
        target_identity = _symlink_target_identity(path, resolved, path.parent)
        return (
            f"symlink\0mode={mode:o}\0content={_file_digest(resolved)}\0"
            f"target_mode={stat.S_IMODE(resolved_info.st_mode):o}\0"
            f"target_identity={target_identity}"
        )
    if not stat.S_ISREG(info.st_mode):
        _die(f"cache key file is missing or unsafe: {path}")
    return f"file\0mode={mode:o}\0content={_file_digest(path)}"


def _tree_digest(root: Path) -> str:
    try:
        root_info = root.lstat()
    except OSError as exc:
        _die(f"cache key tree is missing: {root}: {exc}")
    if not stat.S_ISDIR(root_info.st_mode) or stat.S_ISLNK(root_info.st_mode):
        _die(f"cache key tree is not a real directory: {root}")
    root_mode = stat.S_IMODE(root_info.st_mode)
    digest = hashlib.sha256(f"tree-digest-v6\nroot\0mode={root_mode:o}\n".encode())

    def visit(directory: Path, relative: Path, ancestors: frozenset[tuple[int, int]]) -> None:
        try:
            directory_info = directory.stat()
        except OSError as exc:
            _die(f"cannot stat cache key tree directory {directory}: {exc}")
        identity = (directory_info.st_dev, directory_info.st_ino)
        if identity in ancestors:
            _die(f"cache key tree contains a symlink cycle: {directory}")
        descendants = ancestors | {identity}
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            _die(f"cannot scan cache key tree {directory}: {exc}")
        for entry in children:
            rel = relative / entry.name
            if entry.name == ".git":
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                _die(f"cannot stat cache key tree entry {entry.path}: {exc}")
            rel_bytes = rel.as_posix().encode("utf-8")
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISLNK(info.st_mode):
                raw_target = os.readlink(entry.path)
                try:
                    resolved = Path(entry.path).resolve(strict=False)
                    resolved.relative_to(root.resolve())
                    inside_root = True
                except (OSError, ValueError):
                    inside_root = False
                if not inside_root:
                    logical_target = f"<external>/{PurePosixPath(raw_target).name}"
                    target_identity = hashlib.sha256(
                        b"external-symlink-v1\0" + os.fsencode(logical_target)
                    ).hexdigest()
                    digest.update(
                        b"symlink-external\0" + rel_bytes
                        + f"\0link_mode={mode:o}\0target_identity={target_identity}\n".encode()
                    )
                    continue
                try:
                    resolved = Path(entry.path).resolve(strict=True)
                    resolved_info = resolved.stat()
                except OSError as exc:
                    _die(f"cache key tree symlink target is unsafe: {entry.path}: {exc}")
                target_mode = stat.S_IMODE(resolved_info.st_mode)
                target_identity = _symlink_target_identity(Path(entry.path), resolved, root)
                if stat.S_ISDIR(resolved_info.st_mode):
                    digest.update(
                        b"symlink-dir\0" + rel_bytes
                        + f"\0link_mode={mode:o}\0target_mode={target_mode:o}"
                          f"\0target_identity={target_identity}\n".encode()
                    )
                    visit(resolved, rel, descendants)
                elif stat.S_ISREG(resolved_info.st_mode):
                    digest.update(
                        b"symlink-file\0" + rel_bytes
                        + f"\0link_mode={mode:o}\0target_mode={target_mode:o}"
                          f"\0target_identity={target_identity}\0content=".encode()
                    )
                    digest.update(_file_digest(resolved).encode() + b"\n")
                else:
                    _die(f"unsupported cache key tree symlink target: {entry.path}")
            elif stat.S_ISDIR(info.st_mode):
                digest.update(b"dir\0" + rel_bytes + f"\0mode={mode:o}\n".encode())
                visit(Path(entry.path), rel, descendants)
            elif stat.S_ISREG(info.st_mode):
                digest.update(b"file\0" + rel_bytes + f"\0mode={mode:o}\0content=".encode())
                digest.update(_file_digest(Path(entry.path)).encode() + b"\n")
            else:
                _die(f"unsupported cache key tree entry: {entry.path}")

    visit(root, Path("."), frozenset())
    return digest.hexdigest()


def _key(args: argparse.Namespace) -> None:
    _safe_component(args.component)
    files = _parse_inputs(args.file, "--file")
    trees = _parse_inputs(args.tree, "--tree")
    labels = [label for label, _ in files] + [label for label, _ in trees]
    if len(labels) != len(set(labels)):
        _die("duplicate logical input label across --file/--tree")
    lines = [f"schema={SCHEMA}", f"component={args.component}"]
    for value in sorted(args.value):
        if "\n" in value or "\x00" in value:
            _die("cache key values may not contain newline or NUL")
        lines.append(f"value={value}")
    for label, path in sorted(files):
        lines.append(f"file\0logical={label}\0{_key_file_digest(path)}")
    for label, path in sorted(trees):
        lines.append(f"tree\0logical={label}\0digest={_tree_digest(path)}")
    print(hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest())


@contextmanager
def _directory_path(path: Path, *, create: bool = False, mode: int = 0o755):
    absolute = _absolute(path)
    descriptor = os.open(absolute.anchor, _DIR_FLAGS)
    try:
        for part in absolute.parts[1:]:
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    _die(f"directory is missing: {absolute}")
                try:
                    os.mkdir(part, mode, dir_fd=descriptor)
                except FileExistsError:
                    pass
                try:
                    child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                except OSError as exc:
                    _die(f"unsafe directory path component {part!r} in {absolute}: {exc}")
            except OSError as exc:
                _die(f"unsafe directory path component {part!r} in {absolute}: {exc}")
            os.close(descriptor)
            descriptor = child
        yield descriptor, absolute
    finally:
        os.close(descriptor)


def _open_child_directory(parent_fd: int, name: str, *, create: bool = False, mode: int = 0o755) -> int:
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, mode, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)


def _require_owned_cache_directory(descriptor: int, label: str) -> None:
    info = os.fstat(descriptor)
    if info.st_uid != os.geteuid():
        _die(f"cache {label} is not owned by the current user")
    if stat.S_IMODE(info.st_mode) & 0o022:
        _die(f"cache {label} is writable by group or others")
    os.fchmod(descriptor, 0o700)


def _random_name(prefix: str) -> str:
    return f".{prefix}-{os.getpid()}-{time.time_ns()}-{secrets.token_hex(6)}"


def _entry_info(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _remove_tree_at(parent_fd: int, name: str) -> None:
    info = _entry_info(parent_fd, name)
    if info is None:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        child_fd = _open_child_directory(parent_fd, name)
        try:
            os.fchmod(child_fd, 0o700)
            for child in os.listdir(child_fd):
                _remove_tree_at(child_fd, child)
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=parent_fd)
    else:
        os.unlink(name, dir_fd=parent_fd)


def _copy_file_fd(source_fd: int, destination_fd: int) -> None:
    while True:
        block = os.read(source_fd, 1024 * 1024)
        if not block:
            return
        view = memoryview(block)
        while view:
            written = os.write(destination_fd, view)
            view = view[written:]


def _copy_tree_fd(source_fd: int, destination_fd: int) -> None:
    source_root = os.fstat(source_fd)
    if not stat.S_ISDIR(source_root.st_mode):
        _die("copy source root is not a directory")
    for name in sorted(os.listdir(source_fd)):
        try:
            info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as exc:
            _die(f"cannot stat cache tree entry {name!r}: {exc}")
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISLNK(info.st_mode):
            _die(f"cache tree contains symlink: {name}")
        if stat.S_ISDIR(info.st_mode):
            source_child = _open_child_directory(source_fd, name)
            try:
                os.mkdir(name, mode, dir_fd=destination_fd)
                destination_child = _open_child_directory(destination_fd, name)
                try:
                    _copy_tree_fd(source_child, destination_child)
                    os.fchmod(destination_child, mode)
                finally:
                    os.close(destination_child)
            finally:
                os.close(source_child)
        elif stat.S_ISREG(info.st_mode):
            try:
                source_file = os.open(name, _FILE_FLAGS, dir_fd=source_fd)
            except OSError as exc:
                _die(f"cannot safely open cache tree file {name!r}: {exc}")
            try:
                opened_info = os.fstat(source_file)
                if not stat.S_ISREG(opened_info.st_mode):
                    _die(f"cache tree file changed type during copy: {name}")
                destination_file = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                    mode,
                    dir_fd=destination_fd,
                )
                try:
                    _copy_file_fd(source_file, destination_file)
                    os.fchmod(destination_file, mode)
                finally:
                    os.close(destination_file)
            finally:
                os.close(source_file)
        else:
            _die(f"unsupported cache tree entry: {name}")
    os.fchmod(destination_fd, stat.S_IMODE(source_root.st_mode))


def _entries_fd(root_fd: int) -> list[dict]:
    entries: list[dict] = []

    def visit(directory_fd: int, relative: Path) -> None:
        for name in sorted(os.listdir(directory_fd)):
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            rel = relative / name
            if stat.S_ISLNK(info.st_mode):
                _die(f"cache tree contains symlink: {rel.as_posix()}")
            if stat.S_ISDIR(info.st_mode):
                entries.append({
                    "path": rel.as_posix(), "type": "directory",
                    "mode": stat.S_IMODE(info.st_mode), "size": 0, "sha256": None,
                })
                child_fd = _open_child_directory(directory_fd, name)
                try:
                    visit(child_fd, rel)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(info.st_mode):
                file_fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
                try:
                    opened_info = os.fstat(file_fd)
                    if not stat.S_ISREG(opened_info.st_mode):
                        _die(f"cache tree file changed type during manifest: {rel.as_posix()}")
                    entries.append({
                        "path": rel.as_posix(), "type": "file",
                        "mode": stat.S_IMODE(opened_info.st_mode), "size": opened_info.st_size,
                        "sha256": _digest_fd(file_fd),
                    })
                finally:
                    os.close(file_fd)
            else:
                _die(f"unsupported cache tree entry: {rel.as_posix()}")

    visit(root_fd, Path("."))
    return sorted(entries, key=lambda item: item["path"])


def _manifest_fd(root_fd: int, component: str, key: str) -> dict:
    _safe_component(component)
    _safe_key(key)
    info = os.fstat(root_fd)
    if not stat.S_ISDIR(info.st_mode):
        _die("cache tree root is not a directory")
    entries = _entries_fd(root_fd)
    return {
        "schema": SCHEMA,
        "component": component,
        "key": key,
        "root_mode": stat.S_IMODE(info.st_mode),
        "entries": entries,
        "files": entries,
    }


def _manifest_path(root: Path, component: str, key: str) -> dict:
    with _directory_path(root) as (root_fd, _):
        return _manifest_fd(root_fd, component, key)


def _read_json_at(parent_fd: int, name: str, *, required_mode: int | None = None) -> object:
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            _die(f"JSON input is not a regular file: {name}")
        if required_mode is not None and stat.S_IMODE(info.st_mode) != required_mode:
            _die(f"JSON input has unsafe mode: {name}")
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as stream:
            return json.load(stream)
    finally:
        os.close(descriptor)


def _write_json_at(parent_fd: int, name: str, document: object, mode: int = 0o600) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        mode,
        dir_fd=parent_fd,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.fchmod(descriptor, mode)
    finally:
        os.close(descriptor)


@contextmanager
def _key_lock(cache_root: Path, component: str, key: str):
    _safe_component(component)
    _safe_key(key)
    with _directory_path(cache_root, create=True) as (cache_fd, _):
        _require_owned_cache_directory(cache_fd, "root")
        component_fd = _open_child_directory(cache_fd, component, create=True, mode=0o700)
        try:
            _require_owned_cache_directory(component_fd, f"component {component!r}")
            lock_name = f".{key}.lock"
            lock_fd = os.open(
                lock_name,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=component_fd,
            )
            try:
                if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                    _die(f"unsafe lock file: {lock_name}")
                os.fchmod(lock_fd, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                yield component_fd
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        finally:
            os.close(component_fd)


def _load_entry_fd(component_fd: int, component: str, key: str) -> dict | None:
    try:
        entry_fd = _open_child_directory(component_fd, key)
    except OSError:
        return None
    try:
        entry_info = os.fstat(entry_fd)
        if stat.S_IMODE(entry_info.st_mode) != 0o700:
            return None
        if sorted(os.listdir(entry_fd)) != ["manifest.json", "payload"]:
            return None
        manifest = _read_json_at(entry_fd, "manifest.json", required_mode=0o600)
        if not isinstance(manifest, dict):
            return None
        if manifest.get("schema") != SCHEMA or manifest.get("component") != component or manifest.get("key") != key:
            return None
        payload_fd = _open_child_directory(entry_fd, "payload")
        try:
            actual = _manifest_fd(payload_fd, component, key)
        finally:
            os.close(payload_fd)
        return manifest if actual == manifest else None
    except (OSError, ValueError, TypeError, UnicodeError, SystemExit):
        return None
    finally:
        os.close(entry_fd)


def _quarantine_fd(component_fd: int, key: str) -> str:
    for _ in range(100):
        quarantine = _random_name(f"{key}.corrupt")
        try:
            os.rename(key, quarantine, src_dir_fd=component_fd, dst_dir_fd=component_fd)
            return quarantine
        except FileNotFoundError:
            return quarantine
        except FileExistsError:
            continue
    _die(f"unable to quarantine corrupt cache entry: {key}")


def _restore(args: argparse.Namespace) -> None:
    _safe_component(args.component)
    _safe_key(args.key)
    destination = _absolute(Path(args.destination))
    if destination.name in {"", ".", ".."}:
        _die(f"unsafe restore destination: {destination}")
    with _key_lock(Path(args.cache_root), args.component, args.key) as component_fd:
        if _entry_info(component_fd, args.key) is None:
            print("MISS")
            raise SystemExit(3)
        manifest = _load_entry_fd(component_fd, args.component, args.key)
        if manifest is None:
            quarantine = _quarantine_fd(component_fd, args.key)
            print(f"CORRUPT_MISS quarantined={quarantine}")
            raise SystemExit(3)
        entry_fd = _open_child_directory(component_fd, args.key)
        try:
            payload_fd = _open_child_directory(entry_fd, "payload")
            try:
                with _directory_path(destination.parent, create=True) as (destination_parent_fd, _):
                    if _entry_info(destination_parent_fd, destination.name) is not None:
                        _die(f"refusing to overwrite existing restore destination: {destination}")
                    temporary = _random_name(f"{args.component}-restore")
                    os.mkdir(temporary, 0o700, dir_fd=destination_parent_fd)
                    committed = False
                    try:
                        temporary_fd = _open_child_directory(destination_parent_fd, temporary)
                        try:
                            _copy_tree_fd(payload_fd, temporary_fd)
                            if _manifest_fd(temporary_fd, args.component, args.key) != manifest:
                                _die(f"restored component verification failed: {destination}")
                        finally:
                            os.close(temporary_fd)
                        os.rename(
                            temporary, destination.name,
                            src_dir_fd=destination_parent_fd, dst_dir_fd=destination_parent_fd,
                        )
                        committed = True
                    finally:
                        if not committed:
                            _remove_tree_at(destination_parent_fd, temporary)
            finally:
                os.close(payload_fd)
        finally:
            os.close(entry_fd)
    print("HIT")


def _store(args: argparse.Namespace) -> None:
    _safe_component(args.component)
    _safe_key(args.key)
    with _directory_path(Path(args.source)) as (source_fd, _):
        with _key_lock(Path(args.cache_root), args.component, args.key) as component_fd:
            temporary = _random_name(f"{args.component}-store")
            os.mkdir(temporary, 0o700, dir_fd=component_fd)
            committed = False
            try:
                temporary_fd = _open_child_directory(component_fd, temporary)
                try:
                    os.mkdir("payload", 0o700, dir_fd=temporary_fd)
                    payload_fd = _open_child_directory(temporary_fd, "payload")
                    try:
                        _copy_tree_fd(source_fd, payload_fd)
                        manifest = _manifest_fd(payload_fd, args.component, args.key)
                    finally:
                        os.close(payload_fd)
                    _write_json_at(temporary_fd, "manifest.json", manifest, 0o600)
                    os.fchmod(temporary_fd, 0o700)
                finally:
                    os.close(temporary_fd)

                existing = _load_entry_fd(component_fd, args.component, args.key)
                if existing is not None:
                    if existing != manifest:
                        _die(
                            "same cache key produced different output; "
                            f"refusing nondeterministic replacement: {args.component}/{args.key}"
                        )
                    print("EXISTS")
                    return
                if _entry_info(component_fd, args.key) is not None:
                    _quarantine_fd(component_fd, args.key)
                os.rename(temporary, args.key, src_dir_fd=component_fd, dst_dir_fd=component_fd)
                committed = True
                stored = _load_entry_fd(component_fd, args.component, args.key)
                if stored != manifest:
                    _quarantine_fd(component_fd, args.key)
                    _die(f"stored component verification failed: {args.component}/{args.key}")
            finally:
                if not committed:
                    _remove_tree_at(component_fd, temporary)
    print("STORED")


def _load_components_document(parent_fd: int, manifest_name: str) -> dict:
    if _entry_info(parent_fd, manifest_name) is None:
        return {"schema": COMPONENTS_SCHEMA, "components": []}
    try:
        document = _read_json_at(parent_fd, manifest_name)
    except (OSError, ValueError, TypeError, UnicodeError, SystemExit):
        _die(f"invalid run-local component manifest: {manifest_name}")
    if not isinstance(document, dict) or set(document) != {"schema", "components"}:
        _die(f"invalid run-local component manifest: {manifest_name}")
    components = document.get("components")
    if document.get("schema") != COMPONENTS_SCHEMA or not isinstance(components, list):
        _die(f"invalid run-local component manifest: {manifest_name}")
    names: set[str] = set()
    for item in components:
        if (
            not isinstance(item, dict)
            or set(item) != {"name", "key", "status", "root", "root_mode", "outputs"}
            or not isinstance(item.get("name"), str)
            or not _COMPONENT.fullmatch(item["name"])
            or not isinstance(item.get("key"), str)
            or not _KEY.fullmatch(item["key"])
            or item.get("status") not in {"hit", "miss", "rebuilt"}
            or not isinstance(item.get("root"), str)
            or not Path(item["root"]).is_absolute()
            or "\x00" in item["root"]
            or type(item.get("root_mode")) is not int
            or not 0 <= item["root_mode"] <= 0o7777
            or not isinstance(item.get("outputs"), list)
        ):
            _die(f"invalid run-local component record: {manifest_name}")
        if item["name"] in names:
            _die(f"duplicate run-local component manifest entry: {item['name']}")
        names.add(item["name"])
        output_paths: set[str] = set()
        for output in item["outputs"]:
            if not isinstance(output, dict) or set(output) != {"path", "type", "mode", "size", "sha256"}:
                _die(f"invalid run-local component output record: {manifest_name}")
            output_path = output.get("path")
            output_type = output.get("type")
            output_mode = output.get("mode")
            output_size = output.get("size")
            output_sha = output.get("sha256")
            if (
                not isinstance(output_path, str)
                or not _safe_manifest_relative(output_path)
                or output_path in output_paths
                or output_type not in {"file", "directory"}
                or type(output_mode) is not int
                or not 0 <= output_mode <= 0o7777
                or type(output_size) is not int
                or output_size < 0
                or (
                    output_type == "file"
                    and (not isinstance(output_sha, str) or not _KEY.fullmatch(output_sha))
                )
                or (output_type == "directory" and (output_size != 0 or output_sha is not None))
            ):
                _die(f"invalid run-local component output record: {manifest_name}")
            output_paths.add(output_path)
    return document


def _materialize(args: argparse.Namespace) -> None:
    _safe_component(args.component)
    _safe_key(args.key)
    if args.status not in {"hit", "miss", "rebuilt"}:
        _die(f"invalid component status: {args.status}")
    source = _absolute(Path(args.source))
    destination = _absolute(Path(args.destination))
    manifest_path = _absolute(Path(args.manifest))
    if destination.name in {"", ".", ".."} or manifest_path.name in {"", ".", ".."}:
        _die("unsafe materialization destination or manifest")

    with _directory_path(source) as (source_fd, _):
        source_manifest = _manifest_fd(source_fd, args.component, args.key)
        with _directory_path(manifest_path.parent, create=True) as (manifest_parent_fd, _):
            lock_name = f".{manifest_path.name}.lock"
            lock_fd = os.open(
                lock_name,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=manifest_parent_fd,
            )
            try:
                if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                    _die(f"unsafe component manifest lock: {lock_name}")
                os.fchmod(lock_fd, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                document = _load_components_document(manifest_parent_fd, manifest_path.name)
                if any(item.get("name") == args.component for item in document["components"]):
                    _die(f"duplicate run-local component manifest entry: {args.component}")

                component_record = {
                    "name": args.component,
                    "key": args.key,
                    "status": args.status,
                    "root": str(destination),
                    "root_mode": source_manifest["root_mode"],
                    "outputs": source_manifest["entries"],
                }
                document["components"].append(component_record)

                with _directory_path(destination.parent, create=True) as (destination_parent_fd, _):
                    if _entry_info(destination_parent_fd, destination.name) is not None:
                        _die(f"refusing to overwrite run-local component destination: {destination}")
                    temporary_directory = _random_name(f"{args.component}-materialize")
                    temporary_manifest = _random_name(manifest_path.name)
                    os.mkdir(temporary_directory, 0o700, dir_fd=destination_parent_fd)
                    directory_committed = False
                    manifest_committed = False
                    try:
                        temporary_fd = _open_child_directory(destination_parent_fd, temporary_directory)
                        try:
                            _copy_tree_fd(source_fd, temporary_fd)
                            if _manifest_fd(temporary_fd, args.component, args.key) != source_manifest:
                                _die(f"run-local component copy verification failed: {destination}")
                        finally:
                            os.close(temporary_fd)
                        _write_json_at(manifest_parent_fd, temporary_manifest, document, 0o600)
                        os.rename(
                            temporary_directory, destination.name,
                            src_dir_fd=destination_parent_fd, dst_dir_fd=destination_parent_fd,
                        )
                        directory_committed = True
                        try:
                            os.rename(
                                temporary_manifest, manifest_path.name,
                                src_dir_fd=manifest_parent_fd, dst_dir_fd=manifest_parent_fd,
                            )
                            manifest_committed = True
                        except BaseException:
                            _remove_tree_at(destination_parent_fd, destination.name)
                            directory_committed = False
                            raise
                    finally:
                        if not directory_committed:
                            _remove_tree_at(destination_parent_fd, temporary_directory)
                        if not manifest_committed:
                            _remove_tree_at(manifest_parent_fd, temporary_manifest)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
    print("MATERIALIZED")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    key = sub.add_parser("key")
    key.add_argument("--component", required=True)
    key.add_argument("--value", action="append", default=[])
    key.add_argument("--file", action="append", default=[])
    key.add_argument("--tree", action="append", default=[])
    key.set_defaults(function=_key)
    for name, function in (("restore", _restore), ("store", _store)):
        item = sub.add_parser(name)
        item.add_argument("--cache-root", required=True)
        item.add_argument("--component", required=True)
        item.add_argument("--key", required=True)
        if name == "restore":
            item.add_argument("--destination", required=True)
        else:
            item.add_argument("--source", required=True)
        item.set_defaults(function=function)
    materialize = sub.add_parser("materialize")
    materialize.add_argument("--component", required=True)
    materialize.add_argument("--key", required=True)
    materialize.add_argument("--status", required=True)
    materialize.add_argument("--source", required=True)
    materialize.add_argument("--destination", required=True)
    materialize.add_argument("--manifest", required=True)
    materialize.set_defaults(function=_materialize)
    args = parser.parse_args()
    args.function(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
