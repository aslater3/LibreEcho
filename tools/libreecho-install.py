#!/usr/bin/env python3
"""Prepare a verified LibreEcho initial-install bundle without device access."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import grp
import hashlib
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

PHASE = "RELEASE_READY"
SCHEMA = "libreecho-initial-install-v1"
SHA256 = re.compile(r"[0-9a-f]{64}")
RELEASE = re.compile(r"radar-puffin-(?:v[0-9]+\.[0-9]+\.[0-9]+|(?:nightly|build)-[0-9a-f-]+)")
PUBLIC_NAME = re.compile(r"[A-Za-z0-9._-]+")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

BANNER = r"""
 _     _ _               _____      _
| |   (_) |__  _ __ ___  | ____|__  | |__   ___
| |   | | '_ \| '__/ _ \ |  _| / _ \| '_ \ / _ \
| |___| | |_) | | |  __/ | |__| (_) | | | |  __/
|_____|_|_.__/|_|  \___| |_____\___/|_| |_|\___|
""".strip("\n")

_COLOUR_ENABLED = False
COLOUR_GREEN = "\033[92m"
COLOUR_YELLOW = "\033[93m"
COLOUR_RESET = "\033[0m"


class InstallerError(RuntimeError):
    """The bundle, cache, or resumable state does not meet the install contract."""


ACTIVE_LOG_PATH: Path | None = None


class _Tee:
    """Write visible output to both the terminal and the per-run log."""

    def __init__(self, console, logfile):
        self.console = console
        self.logfile = logfile

    def write(self, text: str) -> int:
        self.console.write(text)
        self.logfile.write(ANSI_ESCAPE.sub("", text))
        return len(text)

    def flush(self) -> None:
        self.console.flush()
        self.logfile.flush()

    def isatty(self) -> bool:
        return self.console.isatty()


def _colour(text: str, code: str) -> str:
    if not _COLOUR_ENABLED:
        return text
    return f"{code}{text}{COLOUR_RESET}"


def print_banner() -> None:
    """Print the installer identity before any host/device output."""
    print()
    print(_colour(BANNER, COLOUR_GREEN), flush=True)
    print("LibreEcho initial installer", flush=True)
    print()


def print_brom_action_prompt() -> None:
    """Print the physical BROM entry sequence as a distinct operator prompt."""
    lines = (
        "ACTION REQUIRED - ENTER BROM MODE",
        "",
        "1. Power the Echo off.",
        "2. Connect the USB data pins: D+, D-, and GND.",
        "3. Do not apply power yet.",
        "4. When prompted below, hold the marked short to ground.",
        "5. Apply power while holding the short.",
        "6. Release the short when BROM appears.",
        "   Amonet may also prompt you to release it.",
        "7. Press Enter when Amonet prompts you.",
    )
    print()
    print(_colour("+----------------------------------------------------------+", COLOUR_YELLOW))
    for line in lines:
        print(_colour(f"| {line:<56} |", COLOUR_YELLOW))
    print(_colour("+----------------------------------------------------------+", COLOUR_YELLOW), flush=True)
    print()


def _append_log(text: str) -> None:
    if ACTIVE_LOG_PATH is None:
        return
    try:
        with ACTIVE_LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(text)
            if not text.endswith("\n"):
                stream.write("\n")
    except OSError:
        # Logging must not prevent the guarded install from reporting its real
        # result; console output remains available through _Tee.
        pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_regular(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise InstallerError(f"unsafe or missing regular file: {path}")


BOOT_BYTES = 16 * 1024 * 1024
# The reviewed post-Amonet GPT gives userdata 0x209c00 sectors of 512 bytes.
# This is used only to validate the exact target before formatting userdata.
USERDATA_BYTES = 0x209C00 * 512
# Sparse userdata expands to roughly 1.09 GiB in LK. Do not apply the short
# control-command timeout to this bounded eMMC operation.
USERDATA_FLASH_TIMEOUT = 900
BOOTOPT = b"bootopt=64S3,32N2,32N2"
AMONET_COMMIT = "dfefe52f0eed7296012707cfff1f753b0ea33257"
AMONET_LAUNCHER = "bootrom-k32-native-diag-step.sh"
RELEASE_REPOSITORY = "https://github.com/aslater3/LibreEcho"
AMONET_FILES = {
    AMONET_LAUNCHER: "6e7ee57c42ef8abac8aa76ab8278db712fca501ec8e6b9a14a2239170c6dc52d",
    "modules/main.py": "f6afc444a8ef7cc28ec6d9803b57d52c4ff66a1946afea36853295a83ce73cf9",
    "modules/common.py": "b8e732c276efcee746a47220deae60b3191a20a48ecfbabe2fad8ddcddbc917b",
    "modules/handshake.py": "6b17443c151da247f32be0683bd902f5e6f86e1fa5402f29b33bd549123b1fc3",
    "modules/load_payload.py": "825564d8698bf9ad3e802ed051e262f33a9d0f4c50e20b8fdd46c21d724461d4",
    "modules/logger.py": "6edcab194d402f24805538ab5f16f4331eff6de6bbc5cd760baad7f34961e69d",
    "modules/gpt.py": "39cc7109cc26a56280ff7facd93d0a8f117ada6c56ae112ff556a93d112bc72d",
    "tools/verify-native-k32-diagnostic.py": "14f5ca0112a198d66ad588fcfbca25ac4566cc0415735f42d2c81e2745a75411",
    "inputs/boot-v184-stock32-parity-stock.img": "c0f52a3b079d214495cd3dd22f92fd85695d1b868c58b491a2edb933bc4f6d1a",
    "bin/lk.bin": "5cb92494340417b1e5d18c3eaa34844dbcfec2cc8086451f087867cd06b15472",
    "bin/tz.img": "fe1de9f18aa0f82a308f0c08da3be1f7c7ac2fd65832e26a3a6bdeb0e6e10136",
    "bin/boot-k32-native-evt.img": "13922dcfdb045ba3b67f8709c395254ac7a3582e2819b545adf0f604dae31424",
    "bin/boot-k32-native-diag-wrapper.full.img": "64f14102856bf905073fff756058b2bc175be0888dcb5c060ffb619e004eb72f",
    "bin/boot-k32-native-diag-wrapper.sparse.img": "7a1b548551537b918fb39cddd3b2a00ef380f819a40178bd2982d9c75b291c26",
    "patches/native-k32-diagnostic.patch": "eb14d7973801f1e6800679f424566ce07fa7a956e62edb0719c15e3bf6085635",
    "brom-payload/build/payload.bin": "16ff2539761a85fe6eea0dcb461b3904bfd0f01c431b49010ebeb5fc2407e5e5",
    "bin/preloader.img": "49193a8c06f3ac4c70691cb8bcaa3e2ddcefbd36d54b8d425a014aa2318846ff",
    "bin/boot-k32-native-diag.hdr": "dbbff7eeb8830c0d6cde454a97dc31be73d1cba32e6be9b21fe3c7be2b659066",
    "bin/boot-k32-native-diag.payload": "5e9908c33221c5d39f52e2ffb4fd8c733d55a4b40501074ff12a01ec35a8b9cd",
}
ONE_SHOT_PHASES = {
    "RELEASE_READY", "AMONET_VERIFIED", "AMONET_HANDOFF", "FASTBOOT_READY",
    "BOOT_WRITTEN", "ADB_READY", "READBACK_VERIFIED", "FEATURES_STAGED",
    "WEBUI_FORWARDED",
}


def _sha256_bytes(path: Path) -> str:
    return _sha256(path)


def require_host_commands(*commands: str) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise InstallerError("required host command(s) missing: " + ", ".join(missing))


def _executable_path(command: str) -> Path:
    resolved = shutil.which(command)
    if resolved is None:
        raise InstallerError(f"required host executable is missing: {command}")
    path = Path(resolved).resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise InstallerError(f"required host executable is not runnable: {path}")
    return path


def _copy_executable(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    shutil.copy2(source, temporary)
    temporary.chmod(0o755)
    os.replace(temporary, destination)


def _find_mke2fs(fastboot_source: Path) -> Path | None:
    path_from_path = shutil.which("mke2fs")
    candidates = (
        fastboot_source.parent / "mke2fs",
        Path(path_from_path) if path_from_path else None,
        Path("/usr/sbin/mke2fs"),
        Path("/usr/bin/mke2fs"),
    )
    for candidate in candidates:
        if candidate is not None:
            try:
                candidate = candidate.resolve()
            except OSError:
                continue
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def _find_img2simg() -> Path | None:
    candidate = shutil.which("img2simg")
    if candidate is None:
        return None
    resolved = Path(candidate).resolve()
    if resolved.is_file() and os.access(resolved, os.X_OK):
        return resolved
    return None


def _install_host_format_tools() -> None:
    apt_get = shutil.which("apt-get")
    if apt_get is None:
        raise InstallerError(
            "userdata image tools are missing and apt-get is unavailable; install "
            "e2fsprogs and android-sdk-libsparse-utils manually before starting a hardware run"
        )
    prefix = [] if os.geteuid() == 0 else ([shutil.which("sudo")] if shutil.which("sudo") else None)
    if prefix is None:
        raise InstallerError(
            "userdata image tools are missing and sudo is unavailable; install e2fsprogs "
            "and android-sdk-libsparse-utils manually before starting a hardware run"
        )
    command_prefix = [item for item in prefix if item]
    print(
        "HOST PREFLIGHT: installing missing userdata image tools before device access.",
        flush=True,
    )
    _run_command(command_prefix + [apt_get, "update"], 300)
    _run_command(
        command_prefix
        + [apt_get, "install", "-y", "e2fsprogs", "android-sdk-libsparse-utils"],
        300,
    )


def prepare_fastboot_tools(
    fastboot_bin: str,
    cache_root: Path,
    *,
    install_host_deps: bool = False,
) -> str:
    """Stage the complete host toolset before any device access."""
    fastboot_source = _executable_path(fastboot_bin)
    helper = _find_mke2fs(fastboot_source)
    img2simg = _find_img2simg()
    if (helper is None or img2simg is None) and install_host_deps:
        _install_host_format_tools()
        helper = _find_mke2fs(fastboot_source)
        img2simg = _find_img2simg()
    if helper is None or img2simg is None:
        missing = ", ".join(
            name for name, path in (("mke2fs", helper), ("img2simg", img2simg)) if path is None
        )
        raise InstallerError(
            f"HOST PREFLIGHT: {missing} required for userdata image generation but not found. "
            "Re-run with --install-host-deps or install e2fsprogs and "
            "android-sdk-libsparse-utils manually."
        )
    tool_root = cache_root / "host-tools"
    staged_fastboot = tool_root / "fastboot"
    staged_mke2fs = tool_root / "mke2fs"
    staged_img2simg = tool_root / "img2simg"
    _copy_executable(fastboot_source, staged_fastboot)
    _copy_executable(helper, staged_mke2fs)
    _copy_executable(img2simg, staged_img2simg)
    version = _run_command([str(staged_fastboot), "--version"], 20, check=False)
    if version.returncode != 0:
        raise InstallerError("HOST PREFLIGHT: staged fastboot did not run successfully")
    mke2fs_version = _run_command([str(staged_mke2fs), "-V"], 20, check=False)
    if mke2fs_version.returncode not in (0, 1):
        raise InstallerError("HOST PREFLIGHT: staged mke2fs did not run successfully")
    img2simg_probe = _run_command([str(staged_img2simg)], 20, check=False)
    if img2simg_probe.returncode not in (0, 1, 2):
        raise InstallerError("HOST PREFLIGHT: staged img2simg did not run successfully")
    print(
        f"HOST PREFLIGHT: fastboot={staged_fastboot}, mke2fs={staged_mke2fs}, "
        f"img2simg={staged_img2simg}; userdata image tools ready before device access.",
        flush=True,
    )
    return str(staged_fastboot)


def _run_command(argv: list[str], timeout: float, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    _append_log(f"COMMAND start timeout={timeout:g}: {argv!r}")
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        _append_log(f"COMMAND exception: {type(error).__name__}: {error}")
        raise InstallerError(f"command failed or timed out: {' '.join(argv)}") from error
    _append_log(f"COMMAND result rc={result.returncode}: {argv!r}")
    if result.stdout:
        _append_log("COMMAND stdout:\n" + result.stdout.rstrip("\n"))
    if result.stderr:
        _append_log("COMMAND stderr:\n" + result.stderr.rstrip("\n"))
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise InstallerError(f"command failed ({result.returncode}): {' '.join(argv)}: {detail}")
    return result


def _run_command_with_heartbeat(
    argv: list[str],
    timeout: float,
    message: str,
    *,
    interval: float = 15,
) -> subprocess.CompletedProcess[str]:
    """Run a quiet long command while printing honest elapsed-time heartbeats."""
    _append_log(f"COMMAND start timeout={timeout:g}: {argv!r}")
    started = time.monotonic()
    try:
        process = subprocess.Popen(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        _append_log(f"COMMAND exception: {type(error).__name__}: {error}")
        raise InstallerError(f"command failed or timed out: {' '.join(argv)}") from error
    while True:
        elapsed = time.monotonic() - started
        remaining = timeout - elapsed
        if remaining <= 0:
            process.kill()
            stdout, stderr = process.communicate()
            _append_log(f"COMMAND timeout after {timeout:g}s: {argv!r}")
            if stdout:
                _append_log("COMMAND stdout:\n" + stdout.rstrip("\n"))
            if stderr:
                _append_log("COMMAND stderr:\n" + stderr.rstrip("\n"))
            raise InstallerError(f"command timed out after {timeout:g}s: {' '.join(argv)}")
        try:
            stdout, stderr = process.communicate(timeout=min(interval, remaining))
            break
        except subprocess.TimeoutExpired:
            print(f"{message} ({int(time.monotonic() - started)}s elapsed)", flush=True)
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    _append_log(f"COMMAND result rc={result.returncode}: {argv!r}")
    if result.stdout:
        _append_log("COMMAND stdout:\n" + result.stdout.rstrip("\n"))
    if result.stderr:
        _append_log("COMMAND stderr:\n" + result.stderr.rstrip("\n"))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise InstallerError(f"command failed ({result.returncode}): {' '.join(argv)}: {detail}")
    return result


def _download_url(url: str, destination: Path, label: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    print(f"Downloading {label}...", flush=True)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "LibreEcho-installer/1"})
        with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as stream:
            total = int(response.headers.get("Content-Length", "0") or 0)
            copied = 0
            last_notice = time.monotonic()
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                stream.write(block)
                copied += len(block)
                now = time.monotonic()
                if now - last_notice >= 2:
                    suffix = f"/{total}" if total else ""
                    print(f"  {copied} bytes{suffix}", flush=True)
                    last_notice = now
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
        temporary.unlink(missing_ok=True)
        raise InstallerError(f"download failed: {url}") from error
    return destination


def _github_repo_parts(repository: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(repository.rstrip("/"))
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        raise InstallerError("only HTTPS GitHub repositories are supported for downloads")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or any(not PUBLIC_NAME.fullmatch(part) for part in parts):
        raise InstallerError("malformed GitHub repository URL")
    return parts[0], parts[1].removesuffix(".git")


def download_release(release_tag: str, repository: str, download_root: Path | str) -> Path:
    owner, repo = _github_repo_parts(repository)
    prefix = f"libreecho-{release_tag}"
    destination = Path(download_root) / release_tag
    sums_path = destination / f"{prefix}-SHA256SUMS"
    sums_url = f"https://github.com/{owner}/{repo}/releases/download/{urllib.parse.quote(release_tag)}/{sums_path.name}"
    _download_url(sums_url, sums_path, "release checksums")
    records = _checksums(sums_path, None)
    required = {
        f"{prefix}-boot.img", f"{prefix}-initial-install.tar",
        f"{prefix}-installer.py", f"{prefix}-ota-public-key.hex",
        f"{prefix}-release-notes.md", f"{prefix}.ota.tar",
    }
    required.update(
        f"{prefix}-{feature}.{suffix}"
        for feature in ("airplay2", "assistant", "stt", "tts", "wakeword")
        for suffix in ("squashfs", "manifest.json")
    )
    if not required.issubset(records):
        raise InstallerError(f"release is missing required assets: {sorted(required - set(records))}")
    for name, digest in records.items():
        if name == sums_path.name:
            continue
        path = destination / name
        if path.is_file() and not path.is_symlink() and _sha256(path) == digest:
            print(f"  cached verified {name} sha256={digest}", flush=True)
            continue
        path = _download_url(
            f"https://github.com/{owner}/{repo}/releases/download/{urllib.parse.quote(release_tag)}/{urllib.parse.quote(name)}",
            destination / name, name,
        )
        if _sha256(path) != digest:
            raise InstallerError(f"downloaded release hash mismatch: {name}")
        print(f"  verified {name} sha256={digest}", flush=True)
    return destination


def _safe_extract_archive(archive: Path, destination: Path) -> Path:
    temporary = Path(tempfile.mkdtemp(prefix="amonet.", dir=destination.parent))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            top = None
            seen: set[str] = set()
            for member in members:
                path = pathlib.PurePosixPath(member.name)
                if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                    raise InstallerError("unsafe Amonet archive path")
                if not path.parts:
                    continue
                top = top or path.parts[0]
                if path.parts[0] != top:
                    raise InstallerError("unexpected Amonet archive layout")
                if len(path.parts) == 1:
                    if not member.isdir():
                        raise InstallerError("Amonet archive root is not a directory")
                    continue
                relative = pathlib.PurePosixPath(*path.parts[1:])
                if str(relative) in seen:
                    raise InstallerError("duplicate Amonet archive member")
                seen.add(str(relative))
                if not (member.isdir() or member.isreg()):
                    raise InstallerError("Amonet archive contains a link or special file")
            tar.extractall(temporary)
        extracted = temporary / str(top)
        if not extracted.is_dir():
            raise InstallerError("Amonet archive root is missing")
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(extracted, destination)
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _materialize_amonet_lfs(root: Path, owner: str, repo: str, commit: str) -> None:
    for relative in AMONET_FILES:
        path = root / relative
        _safe_regular(path)
        data = path.read_bytes()
        if not data.startswith(b"version https://git-lfs.github.com/spec/"):
            continue
        url = f"https://media.githubusercontent.com/media/{owner}/{repo}/{commit}/{urllib.parse.quote(relative)}"
        temporary = path.with_name(path.name + ".lfs-part")
        _download_url(url, temporary, f"Amonet LFS {relative}")
        os.replace(temporary, path)


def download_amonet(repository: str, commit: str, download_root: Path | str) -> Path:
    if commit != AMONET_COMMIT:
        raise InstallerError("release names an unsupported Amonet commit")
    owner, repo = _github_repo_parts(repository)
    root = Path(download_root) / "amonet" / commit
    marker = root / ".libreecho-source-commit"
    if root.is_dir() and marker.is_file() and marker.read_text(encoding="ascii").strip() == commit:
        _materialize_amonet_lfs(root, owner, repo, commit)
        return verify_amonet_root(root, commit)
    archive = Path(download_root) / "amonet" / f"{commit}.tar.gz"
    url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{commit}"
    _download_url(url, archive, f"Amonet commit {commit}")
    extracted = _safe_extract_archive(archive, root)
    _materialize_amonet_lfs(extracted, owner, repo, commit)
    marker.write_text(commit + "\n", encoding="ascii")
    marker.chmod(0o600)
    return verify_amonet_root(extracted, commit)


def verify_amonet_root(root: Path | str, expected_commit: str = AMONET_COMMIT) -> Path:
    if expected_commit != AMONET_COMMIT:
        raise InstallerError("release names an unsupported Amonet commit")
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise InstallerError("Amonet root must be a real directory")
    git_dir = root / ".git"
    if git_dir.exists():
        head = _run_command(["git", "-C", str(root), "rev-parse", "HEAD"], 10).stdout.strip()
        if head != expected_commit:
            raise InstallerError(f"Amonet HEAD mismatch: expected {expected_commit}, got {head}")
        status = _run_command(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"], 10).stdout
        if status:
            raise InstallerError("Amonet root is dirty; use the pinned clean checkout")
    else:
        marker = root / ".libreecho-source-commit"
        if not marker.is_file() or marker.read_text(encoding="ascii").strip() != expected_commit:
            raise InstallerError("downloaded Amonet source marker is missing or incorrect")
    for relative, expected in AMONET_FILES.items():
        path = root / relative
        _safe_regular(path)
        if _sha256_bytes(path) != expected:
            raise InstallerError(f"Amonet artifact hash mismatch: {relative}")
    launcher = root / AMONET_LAUNCHER
    if not os.access(launcher, os.X_OK):
        raise InstallerError("Amonet launcher is not executable")
    return root


def fastboot_devices(fastboot_bin: str) -> list[str]:
    result = _run_command([fastboot_bin, "devices"], 10)
    return [line.split()[0] for line in result.stdout.splitlines() if len(line.split()) >= 2 and line.split()[1] == "fastboot"]


def wait_for_fastboot_serial(fastboot_bin: str, requested: str, timeout: float) -> str:
    print("Amonet handoff complete; waiting for fastboot USB re-enumeration.", flush=True)
    deadline = time.monotonic() + timeout
    next_notice = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            devices = fastboot_devices(fastboot_bin)
        except InstallerError:
            devices = []
        if requested != "auto" and requested in devices:
            print(f"Fastboot detected: {requested}", flush=True)
            return requested
        if requested == "auto" and len(devices) == 1:
            print(f"Fastboot detected: {devices[0]}", flush=True)
            return devices[0]
        now = time.monotonic()
        if now >= next_notice:
            remaining = max(0, int(deadline - now))
            print(f"Fastboot status: waiting for device USB ({remaining}s timeout remaining).", flush=True)
            next_notice = now + 10
        time.sleep(1)
    raise InstallerError("timed out waiting for fastboot USB after Amonet handoff")


def adb_devices(adb_bin: str) -> list[str]:
    result = _run_command([adb_bin, "devices"], 10)
    return [line.split()[0] for line in result.stdout.splitlines() if len(line.split()) >= 2 and line.split()[1] == "device"]


def select_adb_serial(adb_bin: str, requested: str) -> str:
    devices = adb_devices(adb_bin)
    if requested == "auto":
        if len(devices) != 1:
            raise InstallerError(f"expected exactly one ADB device, found {len(devices)}")
        return devices[0]
    if requested not in devices:
        raise InstallerError(f"ADB serial is not present: {requested}")
    return requested


def select_fastboot_serial(fastboot_bin: str, requested: str) -> str:
    devices = fastboot_devices(fastboot_bin)
    if requested == "auto":
        if len(devices) != 1:
            raise InstallerError(f"expected exactly one fastboot device, found {len(devices)}")
        return devices[0]
    if requested not in devices:
        raise InstallerError(f"fastboot serial is not present: {requested}")
    return requested


def verify_fastboot_product(fastboot_bin: str, serial: str) -> None:
    result = _run_command([fastboot_bin, "-s", serial, "getvar", "product"], 20, check=False)
    output = f"{result.stdout}\n{result.stderr}"
    if not re.search(r"product:\s*BISCUIT\b", output, re.IGNORECASE):
        raise InstallerError("fastboot product is not BISCUIT")


def _validate_android_sparse_image(path: Path, expected_bytes: int) -> int:
    """Validate sparse geometry and bound the bytes LK must physically program."""
    data = path.read_bytes()
    if len(data) < 28:
        raise InstallerError("generated userdata sparse image has a truncated header")
    magic, major, minor, file_header_size, chunk_header_size, block_size, total_blocks, total_chunks, _ = struct.unpack_from(
        "<IHHHHIIII", data
    )
    if block_size == 0:
        raise InstallerError("generated userdata image has a zero sparse block size")
    expected_blocks, remainder = divmod(expected_bytes, block_size)
    if (
        magic != 0xED26FF3A
        or major != 1
        or minor != 0
        or file_header_size != 28
        or chunk_header_size != 12
        or block_size != 4096
        or remainder
        or total_blocks != expected_blocks
        or total_chunks < 1
    ):
        raise InstallerError("generated userdata image has an invalid Android sparse header")
    offset = file_header_size
    expanded_blocks = 0
    programmed_bytes = 0
    has_dont_care = False
    for _ in range(total_chunks):
        if offset + chunk_header_size > len(data):
            raise InstallerError("generated userdata image has a truncated sparse chunk")
        chunk_type, _, chunk_blocks, total_size = struct.unpack_from("<HHII", data, offset)
        payload_size = total_size - chunk_header_size
        if total_size < chunk_header_size or offset + total_size > len(data):
            raise InstallerError("generated userdata image has an invalid sparse chunk size")
        if chunk_type == 0xCAC1:  # RAW
            if payload_size != chunk_blocks * block_size:
                raise InstallerError("generated userdata image has an invalid RAW chunk")
            programmed_bytes += chunk_blocks * block_size
        elif chunk_type == 0xCAC2:  # FILL
            if payload_size != 4:
                raise InstallerError("generated userdata image has an invalid FILL chunk")
            programmed_bytes += chunk_blocks * block_size
        elif chunk_type == 0xCAC3:  # DONT_CARE
            if payload_size != 0:
                raise InstallerError("generated userdata image has an invalid DONT_CARE chunk")
            has_dont_care = True
        elif chunk_type == 0xCAC4:  # CRC32
            if payload_size != 4 or chunk_blocks != 0:
                raise InstallerError("generated userdata image has an invalid CRC32 chunk")
        else:
            raise InstallerError("generated userdata image has an unknown sparse chunk type")
        expanded_blocks += chunk_blocks
        offset += total_size
    if offset != len(data) or expanded_blocks != total_blocks:
        raise InstallerError("generated userdata image has inconsistent sparse geometry")
    if not has_dont_care or programmed_bytes > 64 * 1024 * 1024:
        raise InstallerError(
            "generated userdata image would make LK program too much data; sparse skip mode failed"
        )
    return programmed_bytes


def format_userdata_in_fastboot(fastboot_bin: str, serial: str, timeout: float) -> None:
    """Build and flash a compatible sparse ext4 filesystem to userdata."""
    print("FASTBOOT STAGE: validating target product and partition geometry.", flush=True)
    verify_fastboot_product(fastboot_bin, serial)
    size = _fastboot_partition_size(fastboot_bin, serial, "userdata")
    if size != USERDATA_BYTES:
        raise InstallerError(
            f"userdata partition size mismatch: expected {USERDATA_BYTES:#x}, got {size:#x}"
        )
    print(
        f"FASTBOOT STAGE: formatting only userdata as ext4 ({size} bytes); "
        "boot, system, persist, and expdb are not being formatted.",
        flush=True,
    )
    tool_root = Path(fastboot_bin).resolve().parent
    mke2fs = tool_root / "mke2fs"
    img2simg = tool_root / "img2simg"
    if not (mke2fs.is_file() and img2simg.is_file()):
        raise InstallerError("staged userdata image tools disappeared after host preflight")
    with tempfile.TemporaryDirectory(prefix="userdata-image-", dir=tool_root) as temporary:
        root = Path(temporary)
        raw = root / "userdata.ext4"
        sparse = root / "userdata.sparse.img"
        with raw.open("wb") as stream:
            stream.truncate(size)
        _run_command(
            [
                str(mke2fs),
                "-F",
                "-t",
                "ext4",
                "-L",
                "LIBREECHO_DATA",
                "-m",
                "0",
                "-O",
                "^64bit,^metadata_csum,^metadata_csum_seed,^orphan_file",
                "-E",
                "lazy_itable_init=0,lazy_journal_init=0",
                str(raw),
            ],
            max(timeout, 300),
        )
        if raw.stat().st_size != size:
            raise InstallerError("generated userdata filesystem has the wrong raw size")
        # img2simg -s may skip only blocks ext4 itself marks free. Materialize
        # every allocated block (including zeroed inode tables) so stale device
        # contents cannot become live filesystem metadata.
        metadata = _run_command(
            ["dumpe2fs", str(raw)], max(timeout, 300)
        ).stdout
        free_blocks = bytearray(size // 4096)
        for line in metadata.splitlines():
            stripped = line.strip()
            if not stripped.startswith("Free blocks:"):
                continue
            for item in stripped.split(":", 1)[1].split(","):
                item = item.strip()
                if not item:
                    continue
                try:
                    start, end = (
                        (int(value) for value in item.split("-", 1))
                        if "-" in item
                        else (int(item), int(item))
                    )
                except ValueError as error:
                    raise InstallerError("dumpe2fs reported an invalid free-block range") from error
                if start < 0 or end < start or end >= len(free_blocks):
                    raise InstallerError("dumpe2fs free-block range exceeds userdata geometry")
                free_blocks[start : end + 1] = b"\1" * (end - start + 1)
        if not any(free_blocks):
            raise InstallerError("dumpe2fs did not report any free userdata blocks")
        descriptor = os.open(raw, os.O_RDWR)
        try:
            block = 0
            while block < len(free_blocks):
                if free_blocks[block]:
                    block += 1
                    continue
                end = block + 1
                while end < len(free_blocks) and not free_blocks[end]:
                    end += 1
                offset = block * 4096
                length = (end - block) * 4096
                os.pwrite(descriptor, os.pread(descriptor, length, offset), offset)
                block = end
        finally:
            os.close(descriptor)
        _run_command([str(img2simg), "-s", str(raw), str(sparse)], max(timeout, 300))
        programmed_bytes = _validate_android_sparse_image(sparse, size)
        print(
            f"FASTBOOT STAGE: generated validated sparse ext4 image ({sparse.stat().st_size} bytes); "
            f"LK will program only {programmed_bytes} bytes. Flashing only userdata.",
            flush=True,
        )
        _run_command_with_heartbeat(
            [fastboot_bin, "-s", serial, "flash", "userdata", str(sparse)],
            max(timeout, USERDATA_FLASH_TIMEOUT),
            "FASTBOOT STAGE: userdata write still in progress; do not disconnect USB",
        )
    print("FASTBOOT STAGE: userdata filesystem format complete.", flush=True)


def _amonet_progress_message(line: str) -> str | None:
    if "Found port =" in line:
        return "BROM USB detected; Amonet is performing the serial handshake."
    if "all good" in line:
        return "BROM payload is active; Amonet is now running device operations."
    stages = (
        ("Clear preloader header", "Amonet is preparing the boot chain; do not disconnect power or USB."),
        ("Check GPT", "Amonet is reading and checking the partition table."),
        ("Check boot0", "Amonet is checking the eMMC boot area."),
        ("Check rpmb", "Amonet is checking RPMB state."),
        ("Downgrade rpmb", "Amonet is applying the RPMB transition."),
        ("Flash tz", "Amonet is writing TrustZone; this can take a few minutes."),
        ("Flash lk", "Amonet is writing LK A/B; this can take a few minutes."),
        ("Inject payload", "Amonet is installing its persistent wrapper."),
        ("Force fastboot", "Amonet is preparing the fastboot handoff."),
        ("Reset BCB", "Amonet is preparing boot control metadata."),
        ("Flash preloader", "Amonet is restoring the signed preloader."),
        ("Reboot to unlocked fastboot", "Amonet is rebooting into unlocked fastboot."),
    )
    for marker, message in stages:
        if marker in line:
            return message
    return None


def _tty_nodes():
    return sorted(pathlib.Path("/dev").glob("ttyACM*"))


def _tty_is_media_tek(node: Path) -> bool:
    """True if /dev/ttyACMn traces to USB vendor 0e8d via sysfs."""
    try:
        resolved = (pathlib.Path("/sys/class/tty") / node.name / "device").resolve(strict=True)
    except OSError:
        return False
    for parent in (resolved,) + tuple(resolved.parents):
        vendor = parent / "idVendor"
        if vendor.is_file():
            try:
                return vendor.read_text(encoding="ascii").strip().lower() == "0e8d"
            except OSError:
                return False
    return False


def brom_permission_preflight() -> None:
    """Fail fast if any MediaTek CDC node is not openable read/write.

    A MediaTek BROM device appears as /dev/ttyACM* owned root:dialout (or
    root:uucp on some distros) with 0660 mode. When the running user cannot
    open it, Amonet's port scan silently skips it and the user is stranded on
    'waiting for BROM' with no error. The only reliable check is the same
    read/write open the session will later perform. Nodes belonging to other
    vendors are ignored - they are never the install target.
    """
    if os.geteuid() == 0:
        return  # root always wins; no false alarm
    blocked = []
    for node in _tty_nodes():
        if not _tty_is_media_tek(node):
            continue
        try:
            fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            blocked.append(node.name)
        except FileNotFoundError:
            continue  # vanished between enumeration and open
        except OSError as error:
            # Node exists but cannot service an open (ENXIO before carrier,
            # EBUSY under another driver). Not a permissions failure; Amonet
            # classifies those itself, so surface and continue.
            print(f"note: {node.name} read/write preflight hit OSError: {error}", flush=True)
        else:
            os.close(fd)
    if not blocked:
        return
    lines = [
        "cannot open MediaTek serial device(s) {} for read/write: permission denied for uid {}.".format(
            ", ".join(blocked), os.getuid()
        ),
        "A BROM session needs read/write access to /dev/ttyACM*.",
        "Fix (persistent): add this user to the serial group and start a NEW login session:",
        "    sudo usermod -aG dialout $USER   # Debian/Ubuntu (uucp on Arch; lock on some distros)",
        "Fix (this command only):",
        "    sudo python3 <installer> one-shot ...",
    ]
    raise InstallerError("\n".join(lines))


def _running_process_names():
    names = set()
    for entry in pathlib.Path("/proc").glob("[0-9]*/comm"):
        try:
            names.add(entry.read_text(encoding="utf-8").strip())
        except OSError:
            continue
    return names


def _media_tek_usb_devices(root=None):
    """Return {sysfs_slot: (vid, pid)} for enumerated MediaTek devices."""
    base = pathlib.Path("/sys/bus/usb/devices") if root is None else root
    found = {}
    try:
        vendor_paths = list(base.glob("*/idVendor"))
    except OSError:
        return found
    for vendor_path in vendor_paths:
        try:
            product_path = vendor_path.with_name("idProduct")
            vid = vendor_path.read_text(encoding="ascii").strip().lower()
            pid = product_path.read_text(encoding="ascii").strip().lower()
        except OSError:
            continue
        if vid == "0e8d":
            found[vendor_path.parent.name] = (vid, pid)
    return found


def _print_brom_transport_diagnostics() -> None:
    """Classify host-side BROM transport state; best-effort, never raises."""
    try:
        mt = _media_tek_usb_devices()
        brom_present = any(pid == "0003" for _, pid in mt.values())
        all_nodes = [node.name for node in _tty_nodes()]
        nodes = [name for name in all_nodes if _tty_is_media_tek(pathlib.Path("/dev") / name)]
        unrelated = [name for name in all_nodes if name not in nodes]
        print("--- BROM transport diagnostics ---", flush=True)
        if not mt:
            print("no MediaTek USB device is currently enumerated.", flush=True)
        for slot, (vid, pid) in sorted(mt.items()):
            meaning = {
                ("0e8d", "0003"): "MediaTek BROM (correct state)",
                ("0e8d", "2000"): "stock preloader, NOT BROM (short/power-on sequence did not hold)",
                ("0e8d", "2001"): "preloader, NOT BROM",
                ("0e8d", "2008"): "AEOOT (deep error state; see docs)",
            }.get((vid, pid), "MediaTek device in an unrecognized state")
            print(f"usb {slot}: {vid}:{pid} - {meaning}", flush=True)
        if nodes:
            print(f"MediaTek serial nodes present: {', '.join(nodes)}", flush=True)
            if brom_present:
                print("BROM transport is healthy; no transport action is needed.", flush=True)
                print("--- end BROM diagnostics ---", flush=True)
                return
        elif brom_present and unrelated:
            print(
                "the /dev/ttyACM* nodes present ({}) are NOT MediaTek devices - "
                "BROM has no usable serial node.".format(", ".join(unrelated)),
                flush=True,
            )
        elif brom_present:
            print("no /dev/ttyACM* node exists on this host.", flush=True)
        elif unrelated:
            print(f"(unrelated serial nodes ignored: {', '.join(unrelated)})", flush=True)
        else:
            print("no /dev/ttyACM* node exists on this host.", flush=True)
        if brom_present and not nodes:
            print(
                "BROM is enumerated but has no CDC serial node: check 'lsmod | grep cdc_acm', "
                "check dmesg for 'usb ... attached', and confirm no other service grabbed the port.",
                flush=True,
            )
        if "ModemManager" in _running_process_names():
            print(
                "ModemManager is running; it can open and hold new ttyACM ports. "
                "If it repeatedly probes the BROM port, consider temporarily stopping it "
                "(sudo systemctl stop ModemManager) during the handoff.",
                flush=True,
            )
        print(
            "remedy if nothing helps: cold power-off, apply the CLK-to-GND short, keep it applied "
            "while reconnecting power/USB, and only then start the installer.",
            flush=True,
        )
        print("--- end BROM diagnostics ---", flush=True)
    except Exception as error:  # diagnostics must never mask the real failure
        print(f"(BROM transport diagnostics unavailable: {error})", flush=True)


def run_amonet_with_progress(launcher: Path, cwd: Path, timeout: float) -> None:
    brom_permission_preflight()
    print("Amonet handoff started; monitoring its live progress.", flush=True)
    print_brom_action_prompt()
    print("Amonet status: waiting for BROM/USB...", flush=True)
    try:
        process = subprocess.Popen(["bash", str(launcher)], cwd=cwd)
    except OSError as error:
        raise InstallerError("could not start Amonet handoff") from error
    log_path = cwd / "modules" / "amonet.log"
    log_offset = log_path.stat().st_size if log_path.exists() else 0
    last_state = "Waiting for BROM/USB..."
    last_notice = time.monotonic()
    deadline = time.monotonic() + timeout
    next_notice = time.monotonic() + 10
    usb_stall_reported = False
    last_permission_check = 0.0
    while True:
        if log_path.exists():
            with log_path.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(log_offset)
                # Iterating a text file reads ahead, so tell() inside a
                # `for line in stream` loop reports the end of the buffer
                # rather than the end of this line. log_offset then jumped
                # past lines that had not been examined yet and the next
                # seek() skipped them, silently dropping progress states.
                # readline() keeps tell() meaningful.
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    log_offset = stream.tell()
                    message = _amonet_progress_message(line)
                    if message and message != last_state:
                        print(message, flush=True)
                        last_state = message
                        last_notice = time.monotonic()
                        next_notice = last_notice + 10
                        usb_stall_reported = False
        returncode = process.poll()
        if returncode is not None:
            if returncode != 0:
                if last_state == "Waiting for BROM/USB...":
                    _print_brom_transport_diagnostics()
                raise InstallerError(f"Amonet handoff failed with exit code {returncode}")
            return
        now = time.monotonic()
        if now - last_permission_check >= 5:
            # The device is connected AFTER the script starts, so this must
            # re-check during the wait, not just once at launch. If the user
            # cannot open a freshly enumerated MediaTek CDC node, stop now
            # with an actionable message instead of a silent 900s hang.
            last_permission_check = now
            try:
                brom_permission_preflight()
            except InstallerError:
                process.kill()
                process.wait()
                _print_brom_transport_diagnostics()
                raise
        if not usb_stall_reported and last_state == "Waiting for BROM/USB..." and now - last_notice >= 30:
            # Still at 'waiting for BROM' after 30s: classify what the host
            # actually sees so the user learns WHY, instead of staring at the
            # spinner until the 900s timeout.
            _print_brom_transport_diagnostics()
            usb_stall_reported = True
        if now >= deadline:
            process.kill()
            process.wait()
            raise InstallerError("Amonet handoff timed out while waiting for BROM/USB")
        if now >= next_notice:
            remaining = max(0, int(deadline - now))
            print(f"Amonet status: {last_state} ({remaining}s timeout remaining).", flush=True)
            next_notice = now + 10
        time.sleep(min(0.5, max(0.05, next_notice - now)))


def verify_adb_payload_readback(adb_bin: str, serial: str, slot: str, expected_sha256: str, timeout: float = 60) -> None:
    if slot not in {"a", "b"}:
        raise InstallerError("invalid readback slot")
    part = "10" if slot == "a" else "11"
    expected_name = f"boot_{slot}_x"
    uevent = _run_command(
        [adb_bin, "-s", serial, "shell", "cat", f"/sys/class/block/mmcblk0p{part}/uevent"],
        timeout,
    ).stdout
    if f"PARTNAME={expected_name}" not in uevent or f"PARTN={part}" not in uevent:
        raise InstallerError(f"payload partition identity mismatch: mmcblk0p{part}")
    result = _run_command(
        [adb_bin, "-s", serial, "shell", "sha256sum", f"/dev/mmcblk0p{part}"],
        timeout,
    )
    digest = re.search(r"\b([0-9a-f]{64})\b", result.stdout, re.IGNORECASE)
    if digest is None or digest.group(1).lower() != expected_sha256.lower():
        raise InstallerError(f"{expected_name} readback hash mismatch")


def collect_adb_diagnostics(adb_bin: str, serial: str, timeout: float = 30, reason: str = "post-ADB") -> None:
    """Capture read-only target state for sharing after bring-up or failure."""
    _append_log(f"ADB_DIAGNOSTICS begin reason={reason!r} serial={serial!r}")
    commands = (
        ("id", ["id"]),
        ("uname", ["uname", "-a"]),
        ("cmdline", ["cat", "/proc/cmdline"]),
        ("mounts", ["cat", "/proc/mounts"]),
        ("userdata-node", ["ls", "-l", "/dev/mmcblk0p16", "/data"]),
        ("userdata-blkid", ["blkid", "/dev/mmcblk0p16"]),
        ("partitions", ["cat", "/proc/partitions"]),
        ("dmesg-storage", ["dmesg"]),
    )
    for label, remote in commands:
        result = _run_command([adb_bin, "-s", serial, "shell", *remote], timeout, check=False)
        _append_log(f"ADB_DIAGNOSTIC {label} rc={result.returncode}")
        if label == "dmesg-storage":
            lines = [
                line for line in (result.stdout + "\n" + result.stderr).splitlines()
                if re.search(r"mmc|ext4|f2fs|userdata|mount|superblock|I/O error", line, re.IGNORECASE)
            ]
            _append_log("ADB_DIAGNOSTIC dmesg-storage-filtered:\n" + "\n".join(lines[-200:]))
    _append_log("ADB_DIAGNOSTICS end")


def wait_for_transport(probe: list[str], expected: str, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = max(1.0, deadline - time.monotonic())
        try:
            result = subprocess.run(probe, text=True, capture_output=True, timeout=remaining)
        except subprocess.TimeoutExpired:
            result = None
        if result is not None and result.returncode == 0 and result.stdout.strip() == expected:
            return
        time.sleep(1)
    raise InstallerError(f"timed out waiting for {label}")


def fastboot_flash_plan(serial: str, boot: Path, slots: str) -> list[list[str]]:
    if slots not in {"a", "b", "both"}:
        raise InstallerError("slots must be a, b, or both")
    selected = ("a", "b") if slots == "both" else (slots,)
    fb = ["fastboot", "-s", serial]
    return [*([fb + ["flash", f"boot_{slot}", str(boot)] for slot in selected]), fb + ["erase", "expdb"]]


def adb_forward_command(adb_bin: str, serial: str, local_port: int) -> list[str]:
    if not 1024 <= local_port <= 65535:
        raise InstallerError("local port must be between 1024 and 65535")
    return [adb_bin, "-s", serial, "forward", f"tcp:{local_port}", "tcp:8080"]


def validate_public_boot_image(path: Path, expected_sha256: str) -> None:
    """Accept only the complete verified ARMv7 6.1 Android-v0 image."""
    _safe_regular(path)
    if path.stat().st_size != BOOT_BYTES or _sha256(path) != expected_sha256:
        raise InstallerError("published boot image digest or size mismatch")
    with path.open("rb") as stream:
        header = stream.read(576)
    if (len(header) != 576 or header[:8] != b"ANDROID!"
            or not struct.unpack_from("<I", header, 8)[0]
            or not header[64:576].startswith(BOOTOPT)):
        raise InstallerError("published boot image has an unsupported boot contract")


def fastboot_plan(slot: str, boot: Path, misc: Path, format_userdata: bool) -> list[tuple[str, ...]]:
    if slot not in {"a", "b"}:
        raise InstallerError("unsupported target slot")
    # ponytail: fixed tuples are the complete public write allowlist.
    plan = [("format:ext4", "userdata")] if format_userdata else []
    plan.extend((("flash", "misc", str(misc)), ("flash", f"boot_{slot}", str(boot)), ("erase", "expdb")))
    return plan


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


def _checksums(path: Path, expected_names: set[str] | None) -> dict[str, str]:
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
    if expected_names is not None and set(records) != expected_names:
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
    if (not isinstance(value, dict) or not {"phase", "release", "bundle_sha256"}.issubset(value)
            or set(value) - {"phase", "release", "bundle_sha256", "userdata_formatted"}
            or value["phase"] not in ONE_SHOT_PHASES or not isinstance(value["release"], str)
            or not RELEASE.fullmatch(value["release"])
            or not isinstance(value["bundle_sha256"], str) or not SHA256.fullmatch(value["bundle_sha256"])
            or ("userdata_formatted" in value and not isinstance(value["userdata_formatted"], bool))):
        raise InstallerError("malformed installer state")
    return value


def _write_state(path: Path, state: dict[str, Any]) -> None:
    # Preserve the durable userdata-format marker through later phase updates,
    # but never carry it into a newly-started release state unless the caller
    # explicitly sets it.
    if "userdata_formatted" not in state and path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if (previous.get("release") == state.get("release")
                and previous.get("userdata_formatted") is True):
            state = dict(state)
            state["userdata_formatted"] = True
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
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
    ota_asset = release_dir / f"{prefix}.ota.tar"
    if ota_asset.exists():
        _safe_regular(ota_asset)
        expected.add(ota_asset.name)
    records = _checksums(checksums, None)
    optional = {
        "libreecho-radar-puffin-dev.ota.tar",
        f"{prefix}-build.json",
        f"{prefix}-run-one-shot.sh",
    }
    if release_tag.startswith(("radar-puffin-nightly-", "radar-puffin-build-")):
        optional.update({f"{prefix}-build.json", f"{prefix}-verification.txt", f"{prefix}-run-one-shot.sh"})
    unexpected = set(records) - expected - optional
    if not expected.issubset(records) or unexpected:
        raise InstallerError("checksum inventory mismatch")
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


def one_shot(
    release_dir: Path | str | None,
    amonet_root: Path | str | None,
    *,
    release_repository: str = RELEASE_REPOSITORY,
    download_root: Path | str = Path.home() / ".cache/libreecho-installer/downloads",
    cache_root: Path | str = Path.home() / ".cache/libreecho-installer",
    state_root: Path | str = Path.home() / ".local/state/libreecho-installer",
    install_id: str = "default",
    release_tag: str,
    fastboot_bin: str = "fastboot",
    adb_bin: str = "adb",
    fastboot_serial: str = "auto",
    slots: str = "both",
    local_port: int = 18080,
    amonet_timeout: float = 900,
    fastboot_timeout: float = 120,
    adb_timeout: float = 180,
    open_browser: bool = True,
    execute_hardware: bool = False,
    install_host_deps: bool = False,
    emulator_root: Path | str | None = None,
    emulator_kernel: Path | str | None = None,
    emulator_initramfs: Path | str | None = None,
) -> dict[str, str]:
    """Run Amonet, install logical boot payloads, and open first-boot setup."""
    if not execute_hardware:
        raise InstallerError("one-shot requires --execute-hardware")
    cache_root = Path(cache_root)
    state_root = Path(state_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    if emulator_root is not None:
        emulator_root = Path(emulator_root)
        tool = emulator_root / "emulator_tool.py"
        _safe_regular(tool)
        if emulator_kernel is None or emulator_initramfs is None:
            raise InstallerError("emulator mode requires --emulator-kernel and --emulator-initramfs")
        fastboot_bin = str(emulator_root / "mock-fastboot")
        adb_bin = str(emulator_root / "mock-adb")
        require_host_commands("bash", str(tool))
    else:
        require_host_commands("bash", fastboot_bin, adb_bin)
        fastboot_bin = prepare_fastboot_tools(
            fastboot_bin, cache_root, install_host_deps=install_host_deps
        )
    state_root = Path(state_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    lock_path = cache_root / ".lock"
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallerError("another installer is already running") from error
        if not release_tag:
            raise InstallerError("one-shot requires --release-tag")
        if release_dir is None:
            release_dir = download_release(release_tag, release_repository, download_root)
        else:
            release_dir = Path(release_dir)
        manifest, bundle = _prepare(release_dir, cache_root, release_tag)
        boot = cache_root / release_tag / "bundle" / manifest["boot"]["name"]
        validate_public_boot_image(boot, manifest["boot"]["sha256"])
        state_path = _state_path(state_root, install_id)
        bundle_sha = _sha256(bundle)
        _write_state(state_path, {"phase": "RELEASE_READY", "release": release_tag, "bundle_sha256": bundle_sha, "userdata_formatted": False})
        expected_amonet = manifest["amonet"]["commit"]
        if emulator_root is not None:
            emulator_root = Path(emulator_root)
            tool = emulator_root / "emulator_tool.py"
            _safe_regular(tool)
            os.environ["LIBREECHO_EMULATOR_ROOT"] = str(emulator_root)
            os.environ["LIBREECHO_EMULATOR_BOOT"] = str(boot)
            if emulator_kernel is None or emulator_initramfs is None:
                raise InstallerError("emulator mode requires --emulator-kernel and --emulator-initramfs")
            os.environ["LIBREECHO_EMULATOR_KERNEL"] = str(emulator_kernel)
            os.environ["LIBREECHO_EMULATOR_INITRAMFS"] = str(emulator_initramfs)
            for name, mode in (("mock-fastboot", "mock-fastboot"), ("mock-adb", "mock-adb")):
                wrapper = emulator_root / name
                wrapper.write_text(f'#!/bin/sh\nexport LIBREECHO_EMULATOR_TOOL={mode}\nexec "{tool}" "$@"\n', encoding="utf-8")
                wrapper.chmod(0o755)
            fastboot_bin = str(emulator_root / "mock-fastboot")
            adb_bin = str(emulator_root / "mock-adb")
            _write_state(state_path, {"phase": "AMONET_VERIFIED", "release": release_tag, "bundle_sha256": bundle_sha})
            _run_command([str(tool)], amonet_timeout)
        else:
            if amonet_root is None:
                amonet = download_amonet(manifest["amonet"]["repository"], expected_amonet, cache_root)
            else:
                amonet = verify_amonet_root(amonet_root, expected_amonet)
            _write_state(state_path, {"phase": "AMONET_VERIFIED", "release": release_tag, "bundle_sha256": bundle_sha})
            launcher = amonet / AMONET_LAUNCHER
            run_amonet_with_progress(launcher, amonet, amonet_timeout)
        _write_state(state_path, {"phase": "AMONET_HANDOFF", "release": release_tag, "bundle_sha256": bundle_sha})
        if slots not in {"a", "b", "both"}:
            raise InstallerError("slots must be a, b, or both")
        selected = ("a", "b") if slots == "both" else (slots,)
        print("FASTBOOT STAGE: waiting for the unlocked fastboot device.", flush=True)
        serial = wait_for_fastboot_serial(fastboot_bin, fastboot_serial, fastboot_timeout)
        print(f"FASTBOOT STAGE: detected device {serial}; starting validated fastboot operations.", flush=True)
        format_userdata_in_fastboot(fastboot_bin, serial, fastboot_timeout)
        _write_state(state_path, {"phase": "AMONET_HANDOFF", "release": release_tag, "bundle_sha256": bundle_sha, "userdata_formatted": True})
        print("FASTBOOT STAGE: verifying boot payload partition geometry.", flush=True)
        _verify_fastboot_payload_geometry(fastboot_bin, serial)
        _write_state(state_path, {"phase": "FASTBOOT_READY", "release": release_tag, "bundle_sha256": bundle_sha})
        for slot in selected:
            print(f"FASTBOOT STAGE: flashing verified boot payload to boot_{slot}.", flush=True)
            _run_command([fastboot_bin, "-s", serial, "flash", f"boot_{slot}", str(boot)], fastboot_timeout)
        print("FASTBOOT STAGE: clearing expdb before reboot.", flush=True)
        _run_command([fastboot_bin, "-s", serial, "erase", "expdb"], fastboot_timeout)
        _write_state(state_path, {"phase": "BOOT_WRITTEN", "release": release_tag, "bundle_sha256": bundle_sha})
        print("FASTBOOT STAGE: rebooting into the installed LibreEcho boot image.", flush=True)
        try:
            subprocess.run([fastboot_bin, "-s", serial, "reboot"], text=True, capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            print("Amonet fastboot reboot did not acknowledge; waiting for ADB anyway.", flush=True)
        print("FASTBOOT STAGE: waiting for ADB after reboot.", flush=True)
        wait_for_transport([adb_bin, "-s", serial, "get-state"], "device", adb_timeout, "ADB")
        print("ADB STAGE: device online; collecting read-only post-bring-up diagnostics.", flush=True)
        collect_adb_diagnostics(adb_bin, serial, min(adb_timeout, 30), "post-ADB bring-up")
        _write_state(state_path, {"phase": "ADB_READY", "release": release_tag, "bundle_sha256": bundle_sha})
        print("PAYLOAD STAGE: verifying boot_a_x and boot_b_x readback.", flush=True)
        for slot in selected:
            verify_adb_payload_readback(adb_bin, serial, slot, manifest["boot"]["sha256"], adb_timeout)
        _write_state(state_path, {"phase": "READBACK_VERIFIED", "release": release_tag, "bundle_sha256": bundle_sha})
        print("PAYLOAD STAGE: beginning verified feature payload staging.", flush=True)
        try:
            stage_device_features(adb_bin, serial, cache_root, manifest, adb_timeout)
        except InstallerError:
            print("PAYLOAD STAGE: failed; collecting read-only ADB diagnostics.", flush=True)
            collect_adb_diagnostics(adb_bin, serial, min(adb_timeout, 30), "feature staging failure")
            raise
        _write_state(state_path, {"phase": "FEATURES_STAGED", "release": release_tag, "bundle_sha256": bundle_sha})
        _run_command(adb_forward_command(adb_bin, serial, local_port), 20)
        url = f"http://127.0.0.1:{local_port}/setup.html"
        _write_state(state_path, {"phase": "WEBUI_FORWARDED", "release": release_tag, "bundle_sha256": bundle_sha})
        if open_browser:
            webbrowser.open(url)
        return {"phase": "WEBUI_FORWARDED", "release": release_tag, "bundle_sha256": bundle_sha, "serial": serial, "url": url}


def _fastboot_partition_size(fastboot_bin: str, serial: str, partition: str) -> int:
    result = _run_command([fastboot_bin, "-s", serial, "getvar", f"partition-size:{partition}"], 20, check=False)
    output = f"{result.stdout}\n{result.stderr}"
    match = re.search(rf"partition-size:{re.escape(partition)}:\s*(?:0x)?([0-9a-fA-F]+)", output, re.IGNORECASE)
    if match is None:
        raise InstallerError(f"fastboot did not report partition size: {partition}")
    return int(match.group(1), 16)


def _verify_fastboot_payload_geometry(fastboot_bin: str, serial: str) -> None:
    for partition in ("boot_a_x", "boot_b_x"):
        size = _fastboot_partition_size(fastboot_bin, serial, partition)
        if size != BOOT_BYTES:
            raise InstallerError(f"{partition} is not the reviewed 16 MiB payload partition: {size:#x}")


def continue_one_shot(
    *,
    cache_root: Path | str,
    state_root: Path | str,
    install_id: str,
    release_tag: str,
    fastboot_bin: str,
    adb_bin: str,
    fastboot_serial: str,
    slots: str,
    fastboot_timeout: float,
    adb_timeout: float,
    local_port: int,
    open_browser: bool,
    execute_hardware: bool,
    repair_userdata: bool = False,
    install_host_deps: bool = False,
) -> dict[str, str]:
    if not execute_hardware:
        raise InstallerError("continuation requires --execute-hardware")
    require_host_commands("bash", fastboot_bin, adb_bin)
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    fastboot_bin = prepare_fastboot_tools(
        fastboot_bin, cache_root, install_host_deps=install_host_deps
    )
    state_path = _state_path(Path(state_root), install_id)
    state = _read_state(state_path)
    if not RELEASE.fullmatch(release_tag):
        raise InstallerError("invalid continuation release tag")
    if state["release"] != release_tag:
        raise InstallerError(
            f"continuation release tag does not match saved state: {release_tag} != {state['release']}"
        )
    if state["phase"] not in {"AMONET_HANDOFF", "ADB_READY", "READBACK_VERIFIED"}:
        raise InstallerError(f"continuation requires AMONET_HANDOFF, ADB_READY, or READBACK_VERIFIED state, got {state['phase']}")
    release = state["release"]
    cache_root = Path(cache_root)
    release_sources = cache_root / "downloads" / release
    if not release_sources.is_dir():
        release_sources = cache_root / release / "downloads"
    manifest, bundle = _prepare(release_sources, cache_root, release)
    if _sha256(bundle) != state["bundle_sha256"]:
        raise InstallerError("cached bundle hash changed since Amonet handoff")
    boot = cache_root / release / "bundle" / manifest["boot"]["name"]
    validate_public_boot_image(boot, manifest["boot"]["sha256"])
    if slots not in {"a", "b", "both"}:
        raise InstallerError("slots must be a, b, or both")
    selected = ("a", "b") if slots == "both" else (slots,)
    phase = state["phase"]
    userdata_formatted = state.get("userdata_formatted", False)
    if phase in {"ADB_READY", "READBACK_VERIFIED"} and not userdata_formatted:
        if not repair_userdata:
            raise InstallerError(
                "saved run reached ADB before userdata was formatted; "
                "rerun continue-one-shot with --repair-userdata to perform "
                "the explicit fastboot userdata format before feature staging"
            )
        serial = select_adb_serial(adb_bin, fastboot_serial)
        print(
            f"RECOVERY STAGE: exact ADB device {serial} selected; "
            "rebooting to fastboot to repair userdata.",
            flush=True,
        )
        _run_command([adb_bin, "-s", serial, "reboot", "bootloader"], 20)
        print("FASTBOOT STAGE: waiting for the repaired device.", flush=True)
        fastboot = wait_for_fastboot_serial(fastboot_bin, fastboot_serial, fastboot_timeout)
        print(f"FASTBOOT STAGE: detected device {fastboot}; formatting userdata.", flush=True)
        format_userdata_in_fastboot(fastboot_bin, fastboot, fastboot_timeout)
        _write_state(state_path, {"phase": "READBACK_VERIFIED", "release": release, "bundle_sha256": state["bundle_sha256"], "userdata_formatted": True})
        print("FASTBOOT STAGE: rebooting after userdata repair.", flush=True)
        try:
            subprocess.run([fastboot_bin, "-s", fastboot, "reboot"], text=True, capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            print("Fastboot reboot did not acknowledge; waiting for ADB anyway.", flush=True)
        print("ADB STAGE: waiting for ADB after userdata repair.", flush=True)
        wait_for_transport([adb_bin, "-s", fastboot, "get-state"], "device", adb_timeout, "ADB")
        serial = select_adb_serial(adb_bin, fastboot)
        collect_adb_diagnostics(adb_bin, serial, min(adb_timeout, 30), "post-userdata repair")
        for slot in selected:
            verify_adb_payload_readback(adb_bin, serial, slot, manifest["boot"]["sha256"], adb_timeout)
        _write_state(state_path, {"phase": "READBACK_VERIFIED", "release": release, "bundle_sha256": state["bundle_sha256"], "userdata_formatted": True})
    elif phase == "AMONET_HANDOFF":
        print("FASTBOOT STAGE: waiting for the unlocked fastboot device.", flush=True)
        serial = wait_for_fastboot_serial(fastboot_bin, fastboot_serial, fastboot_timeout)
        print(f"FASTBOOT STAGE: detected device {serial}; starting validated fastboot operations.", flush=True)
        format_userdata_in_fastboot(fastboot_bin, serial, fastboot_timeout)
        _write_state(state_path, {"phase": "AMONET_HANDOFF", "release": release, "bundle_sha256": state["bundle_sha256"], "userdata_formatted": True})
        print("FASTBOOT STAGE: verifying boot payload partition geometry.", flush=True)
        _verify_fastboot_payload_geometry(fastboot_bin, serial)
        _write_state(state_path, {"phase": "FASTBOOT_READY", "release": release, "bundle_sha256": state["bundle_sha256"]})
        for slot in selected:
            print(f"FASTBOOT STAGE: flashing verified boot payload to boot_{slot}.", flush=True)
            _run_command([fastboot_bin, "-s", serial, "flash", f"boot_{slot}", str(boot)], fastboot_timeout)
        print("FASTBOOT STAGE: clearing expdb before reboot.", flush=True)
        _run_command([fastboot_bin, "-s", serial, "erase", "expdb"], fastboot_timeout)
        _write_state(state_path, {"phase": "BOOT_WRITTEN", "release": release, "bundle_sha256": state["bundle_sha256"]})
        print("FASTBOOT STAGE: rebooting into the installed LibreEcho boot image.", flush=True)
        try:
            subprocess.run([fastboot_bin, "-s", serial, "reboot"], text=True, capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            print("Fastboot reboot did not acknowledge; waiting for ADB anyway.", flush=True)
        print("FASTBOOT STAGE: waiting for ADB after reboot.", flush=True)
        wait_for_transport([adb_bin, "-s", serial, "get-state"], "device", adb_timeout, "ADB")
        print("ADB STAGE: device online; collecting read-only post-bring-up diagnostics.", flush=True)
        collect_adb_diagnostics(adb_bin, serial, min(adb_timeout, 30), "post-ADB bring-up")
        _write_state(state_path, {"phase": "ADB_READY", "release": release, "bundle_sha256": state["bundle_sha256"]})
        print("PAYLOAD STAGE: verifying boot_a_x and boot_b_x readback.", flush=True)
        for slot in selected:
            verify_adb_payload_readback(adb_bin, serial, slot, manifest["boot"]["sha256"], adb_timeout)
        _write_state(state_path, {"phase": "READBACK_VERIFIED", "release": release, "bundle_sha256": state["bundle_sha256"]})
        print("PAYLOAD STAGE: beginning verified feature payload staging.", flush=True)
    else:
        serial = select_adb_serial(adb_bin, fastboot_serial)
        print(f"Resuming from {phase}; no flash or reboot will be attempted ({serial}).", flush=True)
        if phase == "ADB_READY":
            for slot in selected:
                verify_adb_payload_readback(adb_bin, serial, slot, manifest["boot"]["sha256"], adb_timeout)
            _write_state(state_path, {"phase": "READBACK_VERIFIED", "release": release, "bundle_sha256": state["bundle_sha256"]})
    try:
        stage_device_features(adb_bin, serial, cache_root, manifest, adb_timeout)
    except InstallerError:
        print("PAYLOAD STAGE: failed; collecting read-only ADB diagnostics.", flush=True)
        collect_adb_diagnostics(adb_bin, serial, min(adb_timeout, 30), "feature staging failure")
        raise
    _run_command(adb_forward_command(adb_bin, serial, local_port), 20)
    url = f"http://127.0.0.1:{local_port}/setup.html"
    _write_state(state_path, {"phase": "WEBUI_FORWARDED", "release": release, "bundle_sha256": state["bundle_sha256"]})
    if open_browser:
        webbrowser.open(url)
    return {"phase": "WEBUI_FORWARDED", "release": release, "bundle_sha256": state["bundle_sha256"], "serial": serial, "url": url}


def stage_device_features(
    adb_bin: str,
    serial: str,
    cache_root: Path | str,
    manifest: dict[str, Any],
    timeout: float = 180,
) -> None:
    cache_root = Path(cache_root)
    release = manifest["release"]
    payload_root = cache_root / release / "bundle"
    root_script = cache_root / "stage-feature-root.sh"
    root_script.write_text(ROOT_FEATURE_STAGER, encoding="utf-8")
    root_script.chmod(0o755)
    # The stager has to exist ON THE DEVICE before `adb shell sh` can run it.
    # Everything else here is pushed to /tmp first; this file was not, so the
    # shell was handed a laptop path and every feature failed to stage.
    remote_script = "/tmp/libreecho-stage-feature-root.sh"
    _run_command([adb_bin, "-s", serial, "push", str(root_script), remote_script], timeout)
    for feature in manifest["features"]:
        name = feature["name"]
        payload = payload_root / feature["payload"]["name"]
        feature_manifest = payload_root / feature["manifest"]["name"]
        _safe_regular(payload)
        _safe_regular(feature_manifest)
        remote_payload = f"/tmp/libreecho-{name}.squashfs"
        remote_manifest = f"/tmp/libreecho-{name}.manifest.json"
        print(f"Staging feature {name} ({feature['payload']['size']} bytes)...", flush=True)
        _run_command([adb_bin, "-s", serial, "push", str(payload), remote_payload], timeout)
        _run_command([adb_bin, "-s", serial, "push", str(feature_manifest), remote_manifest], timeout)
        config = cache_root / f"stage-{name}.conf"
        config.write_text(
            f"FEATURE_ID={name}\n"
            f"PAYLOAD_SHA256={feature['payload']['sha256']}\n"
            f"PAYLOAD_SIZE={feature['payload']['size']}\n"
            f"PAYLOAD_FILE={remote_payload}\n"
            f"MANIFEST_FILE={remote_manifest}\n",
            encoding="ascii",
        )
        config.chmod(0o600)
        _run_command([adb_bin, "-s", serial, "push", str(config), "/tmp/libreecho-feature-stage.conf"], timeout)
        result = _run_command([adb_bin, "-s", serial, "shell", "sh", remote_script], timeout, check=False)
        if result.returncode != 0 or f"FEATURE_STAGE_OK:{name}" not in result.stdout:
            detail = (result.stderr or result.stdout).strip()[-500:]
            raise InstallerError(f"feature staging failed for {name}: {detail}")
        installed = _run_command(
            [adb_bin, "-s", serial, "shell", "sha256sum", f"/data/libreecho/features/{name}/payload.squashfs"],
            timeout,
        ).stdout
        digest = re.search(r"\b([0-9a-f]{64})\b", installed, re.IGNORECASE)
        if digest is None or digest.group(1).lower() != feature["payload"]["sha256"].lower():
            raise InstallerError(f"feature {name} installed hash mismatch")
        print(f"Feature {name} staged and verified.", flush=True)
        config.unlink(missing_ok=True)


ROOT_FEATURE_STAGER = r"""#!/bin/busybox sh
set -e
BB=/bin/busybox
CONFIG=/tmp/libreecho-feature-stage.conf
[ -r "$CONFIG" ] || { echo FEATURE_STAGE_CONFIG_MISSING; exit 1; }
FEATURE_ID=
PAYLOAD_SHA256=
PAYLOAD_SIZE=
PAYLOAD_FILE=
MANIFEST_FILE=
while IFS='=' read -r key value; do
    case "$key" in
        FEATURE_ID) FEATURE_ID=$value ;;
        PAYLOAD_SHA256) PAYLOAD_SHA256=$value ;;
        PAYLOAD_SIZE) PAYLOAD_SIZE=$value ;;
        PAYLOAD_FILE) PAYLOAD_FILE=$value ;;
        MANIFEST_FILE) MANIFEST_FILE=$value ;;
    esac
done < "$CONFIG"
case "$FEATURE_ID" in ''|*[!a-z0-9._-]*) echo FEATURE_STAGE_ID_INVALID; exit 1 ;; esac
[ -f "$PAYLOAD_FILE" ] || { echo FEATURE_STAGE_PAYLOAD_MISSING; exit 1; }
[ -f "$MANIFEST_FILE" ] || { echo FEATURE_STAGE_MANIFEST_MISSING; exit 1; }
if ! $BB grep -q ' /data ' /proc/mounts 2>/dev/null; then
    [ -b /dev/mmcblk0p16 ] || { echo FEATURE_STAGE_USERDATA_MISSING; exit 1; }
    $BB mkdir -p /data
    $BB mount -t ext4 -o rw,nosuid,nodev,noatime /dev/mmcblk0p16 /data || {
        echo FEATURE_STAGE_USERDATA_MOUNT_FAILED; exit 1; }
else
    $BB mount -o remount,rw /data 2>/dev/null || true
fi
actual=$($BB sha256sum "$PAYLOAD_FILE" | $BB awk '{print $1}')
[ "$actual" = "$PAYLOAD_SHA256" ] || { echo FEATURE_STAGE_PAYLOAD_HASH_MISMATCH; exit 1; }
actual_size=$($BB stat -c %s "$PAYLOAD_FILE" 2>/dev/null)
[ "$actual_size" = "$PAYLOAD_SIZE" ] || { echo FEATURE_STAGE_PAYLOAD_SIZE_MISMATCH; exit 1; }
DEST=/data/libreecho/features/$FEATURE_ID
$BB mkdir -p "$DEST/staging"
$BB cp "$PAYLOAD_FILE" "$DEST/staging/payload.squashfs.new"
staged=$($BB sha256sum "$DEST/staging/payload.squashfs.new" | $BB awk '{print $1}')
[ "$staged" = "$PAYLOAD_SHA256" ] || { echo FEATURE_STAGE_COPY_HASH_MISMATCH; exit 1; }
$BB rm -f "$DEST/payload.squashfs.previous"
if [ -f "$DEST/payload.squashfs" ]; then
    $BB mv "$DEST/payload.squashfs" "$DEST/payload.squashfs.previous"
fi
$BB mv "$DEST/staging/payload.squashfs.new" "$DEST/payload.squashfs"
$BB cp "$MANIFEST_FILE" "$DEST/manifest.json"
$BB sync
$BB rm -f "$CONFIG" "$PAYLOAD_FILE" "$MANIFEST_FILE"
echo "FEATURE_STAGE_OK:$FEATURE_ID"
"""


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
    parser.add_argument("action", choices=("install", "resume", "status", "one-shot", "continue-one-shot"))
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument("--release-tag")
    parser.add_argument("--release-repository", default=RELEASE_REPOSITORY)
    parser.add_argument("--download-root", type=Path, default=Path.home() / ".cache/libreecho-installer/downloads")
    parser.add_argument("--amonet-root", type=Path)
    parser.add_argument("--fastboot-bin", default="fastboot")
    parser.add_argument("--adb-bin", default="adb")
    parser.add_argument("--fastboot-serial", default="auto")
    parser.add_argument("--slots", choices=("a", "b", "both"), default="both")
    parser.add_argument("--local-port", type=int, default=18080)
    parser.add_argument("--amonet-timeout", type=float, default=900)
    parser.add_argument("--fastboot-timeout", type=float, default=120)
    parser.add_argument("--adb-timeout", type=float, default=180)
    parser.add_argument("--no-open-browser", action="store_true")
    parser.add_argument("--execute-hardware", action="store_true")
    parser.add_argument(
        "--install-host-deps", action="store_true",
        help=(
            "install missing e2fsprogs and android-sdk-libsparse-utils before any "
            "device operation (uses apt-get/sudo)"
        ),
    )
    parser.add_argument(
        "--repair-userdata", action="store_true",
        help="explicitly rebuild and flash only userdata when resuming an old failed run",
    )
    parser.add_argument("--emulator-root", type=Path, help="explicit disk-backed BROM/QEMU emulator root")
    parser.add_argument("--emulator-kernel", type=Path)
    parser.add_argument("--emulator-initramfs", type=Path)
    parser.add_argument("--cache-root", type=Path, default=Path.home() / ".cache/libreecho-installer")
    parser.add_argument("--state-root", type=Path, default=Path.home() / ".local/state/libreecho-installer")
    parser.add_argument("--install-id", default="default")
    parser.add_argument(
        "--log-file", type=Path,
        help="persistent console/command log (default: ./libreecho-installer.log)",
    )
    args = parser.parse_args()
    global ACTIVE_LOG_PATH, _COLOUR_ENABLED
    _COLOUR_ENABLED = (
        sys.stdout.isatty()
        and not os.environ.get("NO_COLOR")
        and os.environ.get("TERM") != "dumb"
    )
    ACTIVE_LOG_PATH = args.log_file or Path.cwd() / "libreecho-installer.log"
    log_path = ACTIVE_LOG_PATH
    assert log_path is not None
    if log_path.is_symlink():
        raise SystemExit(f"ERROR: refusing symlink log path: {log_path}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("a", encoding="utf-8") as logfile:
            os.chmod(log_path, 0o600)
            logfile.write("\n=== LibreEcho installer run ===\n")
            logfile.write(f"argv={sys.argv!r}\n")
            logfile.flush()
            with contextlib.redirect_stdout(_Tee(sys.stdout, logfile)), contextlib.redirect_stderr(_Tee(sys.stderr, logfile)):
                print_banner()
                print(f"Installer log: {ACTIVE_LOG_PATH}", flush=True)
                try:
                    if args.action == "install":
                        if args.release_dir is None or args.release_tag is None:
                            raise InstallerError("install requires --release-dir and --release-tag")
                        result = install(args.release_dir, args.cache_root, args.state_root, args.install_id, args.release_tag)
                    elif args.action == "one-shot":
                        if args.release_tag is None:
                            raise InstallerError("one-shot requires --release-tag")
                        result = one_shot(
                            args.release_dir, args.amonet_root,
                            release_repository=args.release_repository, download_root=args.download_root,
                            cache_root=args.cache_root,
                            state_root=args.state_root, install_id=args.install_id,
                            release_tag=args.release_tag, fastboot_bin=args.fastboot_bin,
                            adb_bin=args.adb_bin, fastboot_serial=args.fastboot_serial,
                            slots=args.slots, local_port=args.local_port,
                            amonet_timeout=args.amonet_timeout, fastboot_timeout=args.fastboot_timeout,
                            adb_timeout=args.adb_timeout, open_browser=not args.no_open_browser,
                            execute_hardware=args.execute_hardware or args.emulator_root is not None,
                            install_host_deps=args.install_host_deps,
                            emulator_root=args.emulator_root,
                            emulator_kernel=args.emulator_kernel,
                            emulator_initramfs=args.emulator_initramfs,
                        )
                    elif args.action == "continue-one-shot":
                        result = continue_one_shot(
                            cache_root=args.cache_root, state_root=args.state_root,
                            install_id=args.install_id, release_tag=args.release_tag,
                            fastboot_bin=args.fastboot_bin,
                            adb_bin=args.adb_bin, fastboot_serial=args.fastboot_serial,
                            slots=args.slots, fastboot_timeout=args.fastboot_timeout,
                            adb_timeout=args.adb_timeout, local_port=args.local_port,
                            open_browser=not args.no_open_browser,
                            execute_hardware=args.execute_hardware,
                            repair_userdata=args.repair_userdata,
                            install_host_deps=args.install_host_deps,
                        )
                    elif args.action == "resume":
                        result = resume(args.cache_root, args.state_root, args.install_id)
                    else:
                        result = status(args.state_root, args.install_id)
                except InstallerError as error:
                    print(f"ERROR: {error}", file=sys.stderr, flush=True)
                    raise SystemExit(1) from error
                print(json.dumps(result, sort_keys=True))
    except OSError as error:
        raise SystemExit(f"ERROR: cannot open installer log {ACTIVE_LOG_PATH}: {error}") from error


if __name__ == "__main__":
    main()
