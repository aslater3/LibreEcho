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
import zipfile
from pathlib import Path
from typing import Any

PHASE = "RELEASE_READY"
SCHEMA = "libreecho-initial-install-v1"
SHA256 = re.compile(r"[0-9a-f]{64}")
RELEASE = re.compile(r"radar-puffin-(?:v[0-9]+\.[0-9]+\.[0-9]+|(?:nightly|build)-[0-9a-f-]+)")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
PUBLIC_NAME = re.compile(r"[A-Za-z0-9._-]+")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

BANNER = r"""
#      #####  ####   ####   #####  #####   ####  #   #   ###
#        #    #   #  #   #  #      #      #      #   #  #   #
#        #    ####   ####   ####   ####   #      #####  #   #
#        #    #   #  #  #   #      #      #      #   #  #   #
#####  #####  ####   #   #  #####  #####   ####  #   #   ###
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
# Biscuit has two reviewed stock GPT end-LBA variants. Amonet derives the
# post-wrapper userdata end from the existing GPT, producing either 0x209c00
# or 0x20dc00 sectors of 512 bytes. Keep this allowlist exact so an unknown
# partition layout still fails closed before userdata is written.
USERDATA_BYTES = 0x209C00 * 512
USERDATA_VARIANT_BYTES = 0x20DC00 * 512
USERDATA_SUPPORTED_BYTES = frozenset((USERDATA_BYTES, USERDATA_VARIANT_BYTES))
# Sparse userdata expands to roughly 1.09 GiB in LK. Do not apply the short
# control-command timeout to this bounded eMMC operation.
USERDATA_FLASH_TIMEOUT = 900
BOOTOPT = b"bootopt=64S3,32N2,32N2"
RELEASE_REPOSITORY = "https://github.com/aslater3/LibreEcho"
# Pinned community Amonet ZIPs, identical to the web installer's profiles.js.
# Each target has its own archive. Payloads are chosen by exact lk_build_desc.
AMONET_PINS = {
    "radar_puffin": {
        "archive": "amonet-radar-v1.0.0.zip",
        "archive_size": 58531162,
        "archive_sha256": "ecdb07bc05a508532e5ffed77121592d492b1a91572839e0f17545421f398f1a",
        "lk_builds": {
            "59779ca-20220524_183401": {"payload": "fastbrick-20220524.img", "size": 114509028,
                                        "sha256": "cc78ba7e497b7049361da4e3a85a69ee33e0e6cc5eef89e2752bc8c024f63ffb"},
            "63cb91b-20221007_072309": {"payload": "fastbrick.img", "size": 114294816,
                                        "sha256": "d4001739e752149b0e07ed6c2ffa6fc7ff4cbd08e63e7b6d7251d9bce4d4c494"},
        },
    },
    # Biscuit accepts any LK build. A reviewed build maps to its own payload; any other
    # build gets the archive's default fastbrick.img, as upstream fastbrick.sh does.
    "biscuit": {
        "archive": "amonet-biscuit-v2.0.0.zip",
        "archive_size": 55989416,
        "archive_sha256": "98297293701082bc7272efe077f941c56fc7b6e1f27ef6f2e93b6e4c6fc7b62d",
        "default_payload": {"payload": "fastbrick.img", "size": 114294816,
                            "sha256": "51947565f2ca1a7fc7c0cd4aa888c3e1bbaadb1b135454a720fff36bbeb2ff9e"},
        "lk_builds": {
            "63cb91b-20221007_072309": {"payload": "fastbrick-20221007.img", "size": 114349580,
                                        "sha256": "1100a16f152d713a3c9794f5954c657075db7a602e42f53b6775d4a7f31a4395"},
        },
    },
}
FASTBRICK_RETRY_SECONDS = 2
ONE_SHOT_PHASES = {
    "RELEASE_READY", "AMONET_VERIFIED", "AMONET_HANDOFF", "FASTBOOT_READY",
    "BOOT_WRITTEN", "ADB_READY", "READBACK_VERIFIED", "FEATURES_STAGED",
    "WEBUI_FORWARDED",
}


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


def _find_dumpe2fs() -> Path | None:
    candidate = shutil.which("dumpe2fs")
    for path in (Path(candidate) if candidate else None,
                 Path("/usr/sbin/dumpe2fs"), Path("/sbin/dumpe2fs")):
        if path is not None and path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    return None


def _install_host_format_tools() -> None:
    apt_get = shutil.which("apt-get")
    if apt_get is None:
        raise InstallerError(
            "userdata image tools are missing and apt-get is unavailable; install "
            "e2fsprogs manually before starting a hardware run"
        )
    prefix = [] if os.geteuid() == 0 else ([shutil.which("sudo")] if shutil.which("sudo") else None)
    if prefix is None:
        raise InstallerError(
            "userdata image tools are missing and sudo is unavailable; install "
            "e2fsprogs manually before starting a hardware run"
        )
    command_prefix = [item for item in prefix if item]
    print("HOST PREFLIGHT: installing missing userdata image tools before device access.", flush=True)
    _run_command(command_prefix + [apt_get, "update"], 300)
    _run_command(command_prefix + [apt_get, "install", "-y", "e2fsprogs"], 300)


def prepare_fastboot_tools(
    fastboot_bin: str,
    cache_root: Path,
    *,
    install_host_deps: bool = False,
) -> str:
    """Stage and probe the actual formatter dependencies before device access."""
    fastboot_source = _executable_path(fastboot_bin)
    mke2fs = _find_mke2fs(fastboot_source)
    dumpe2fs = _find_dumpe2fs()
    if (mke2fs is None or dumpe2fs is None) and install_host_deps:
        _install_host_format_tools()
        mke2fs = _find_mke2fs(fastboot_source)
        dumpe2fs = _find_dumpe2fs()
    if mke2fs is None or dumpe2fs is None:
        missing = ", ".join(
            name for name, path in (("mke2fs", mke2fs), ("dumpe2fs", dumpe2fs)) if path is None
        )
        raise InstallerError(
            f"HOST PREFLIGHT: {missing} required for userdata image generation but not found. "
            "Re-run with --install-host-deps or install e2fsprogs manually."
        )
    tool_root = cache_root / "host-tools"
    staged_fastboot = tool_root / "fastboot"
    _copy_executable(fastboot_source, staged_fastboot)
    for name, source in (("mke2fs", mke2fs), ("dumpe2fs", dumpe2fs)):
        _copy_executable(source, tool_root / name)
    for name, args, accepted in (
        ("fastboot", ["--version"], (0,)),
        ("mke2fs", ["-V"], (0, 1)),
        ("dumpe2fs", ["-V"], (0,)),
    ):
        probe = _run_command([str(tool_root / name), *args], 20, check=False)
        if probe.returncode not in accepted:
            raise InstallerError(f"HOST PREFLIGHT: staged {name} did not run successfully")
    print(
        f"HOST PREFLIGHT: fastboot={staged_fastboot}, mke2fs={tool_root / 'mke2fs'}, "
        f"dumpe2fs={tool_root / 'dumpe2fs'}; userdata image tools ready before device access.",
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


def download_release(release_tag: str, repository: str, download_root: Path | str, target: str = "radar_puffin") -> Path:
    owner, repo = _github_repo_parts(repository)
    prefix = target_asset_prefix(release_tag, target)
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
    alias = f"libreecho-{TARGET_INDEX[target]['release_slug']}-stable.ota.tar"
    reuse_alias = release_tag.startswith("radar-puffin-v") and alias in records
    if reuse_alias and records[alias] != records[f"{prefix}.ota.tar"]:
        raise InstallerError("stable OTA alias checksum differs from versioned OTA")
    for name, digest in records.items():
        if name == sums_path.name or (reuse_alias and name == alias):
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
    if reuse_alias:
        # Keep the complete checksum inventory without another transfer or copy.
        alias_path = destination / alias
        alias_path.unlink(missing_ok=True)
        os.link(destination / f"{prefix}.ota.tar", alias_path)
    return destination


def fastboot_devices(fastboot_bin: str) -> list[str]:
    result = _run_command([fastboot_bin, "devices"], 10)
    return [line.split()[0] for line in result.stdout.splitlines() if len(line.split()) >= 2 and line.split()[1] == "fastboot"]


def wait_for_fastboot_serial(fastboot_bin: str, requested: str, timeout: float) -> str:
    print("Brick payload complete; waiting for fastboot USB re-enumeration.", flush=True)
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
    raise InstallerError("timed out waiting for fastboot USB after the brick payload")


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


# Generated from release/targets/*.json; verified by Product target tests.
TARGET_INDEX = {
    "radar_puffin": {"release_slug": "radar-puffin", "fastboot_products": ["RADAR"]},
    "biscuit": {"release_slug": "biscuit", "fastboot_products": ["BISCUIT"]},
}

def fastboot_getvar(fastboot_bin: str, serial: str, name: str) -> str:
    result = _run_command([fastboot_bin, "-s", serial, "getvar", name], 20, check=False)
    match = re.search(rf"^{re.escape(name)}:\s*(.*?)\s*$", f"{result.stdout}\n{result.stderr}", re.MULTILINE)
    return match.group(1) if match else ""


def select_amonet_payload(target: str, lk_build: str) -> dict[str, Any]:
    pin = AMONET_PINS.get(target)
    if pin is None:
        raise InstallerError(f"no pinned Amonet ZIP for target {target}")
    if not lk_build:
        raise InstallerError("LK build is unknown (lk_build_desc empty); refusing to write")
    entry = pin["lk_builds"].get(lk_build) or pin.get("default_payload")
    if entry is None:
        raise InstallerError(f"no pinned fastbrick payload for LK build {lk_build}; refusing to write")
    return {**entry, "build": lk_build}


def identify_kaeru(fastboot_bin: str, serial: str) -> str | None:
    """Return the Kaeru identity if the chain is already converted, else None.

    Stock bootloaders reject `oem kaeru-version`. A converted unit answers OKAY.
    Any other outcome (no reply, timeout, OKAY mixed with FAIL) is indeterminate
    and fails closed, because bricking a converted unit destroys its chain.
    """
    result = _run_command([fastboot_bin, "-s", serial, "oem", "kaeru-version"], 20, check=False)
    output = f"{result.stdout}\n{result.stderr}"
    if "FAIL" in output and "OKAY" not in output:
        return None
    if result.returncode == 0 and "OKAY" in output and "FAIL" not in output:
        info = re.search(r"INFO\s*(.+)", output)
        return info.group(1).strip() if info else "kaeru"
    raise InstallerError("Kaeru identity could not be determined; refusing to brick a possibly converted unit")


def plan_fastbrick(fastboot_bin: str, serial: str, target: str, amonet_zip: "str | Path | None",
                   cache_root: Path) -> tuple[str, Path] | None:
    """Return (lk_build, staged_payload) only when a fastbrick is actually needed.

    Returns None for an already-unlocked or already-Kaeru unit, which must never be
    bricked again. Every refusal happens before any device write.
    """
    # Same strict parse as the web installer's assessIdentity: only a recognised
    # true/false is accepted. An empty or unexpected value refuses before any write.
    unlock_raw = fastboot_getvar(fastboot_bin, serial, "unlock_status").strip().lower()
    if re.fullmatch(r"true|unlocked|yes|1", unlock_raw):
        print("FASTBOOT STAGE: bootloader already reports unlocked; brick payload not needed.", flush=True)
        return None
    if not re.fullmatch(r"false|locked|no|0", unlock_raw):
        raise InstallerError(f"unlock_status {unlock_raw or '(not reported)'!r} is not a recognised true/false value; refusing a brick write")
    kaeru = identify_kaeru(fastboot_bin, serial)
    if kaeru is not None:
        print(f"FASTBOOT STAGE: device already carries Kaeru ({kaeru}); brick payload skipped.", flush=True)
        return None
    if amonet_zip is None:
        raise InstallerError("locked device requires --amonet-zip with the pinned Amonet ZIP")
    lk_build = fastboot_getvar(fastboot_bin, serial, "lk_build_desc")
    payload = select_amonet_payload(target, lk_build)
    staged = extract_amonet_payload(Path(amonet_zip), target, payload, cache_root / "amonet" / target)
    return lk_build, staged


def confirm_post_brick_identity(fastboot_bin: str, pre_serial: str, post_serial: str, target: str) -> None:
    """The device that answers after the brick must be the same serial and product."""
    if post_serial != pre_serial:
        raise InstallerError(f"fastboot serial changed from {pre_serial} to {post_serial} across the brick; refusing to continue")
    verify_fastboot_product(fastboot_bin, post_serial, target)


def extract_amonet_payload(archive: Path, target: str, payload: dict[str, Any], destination_dir: Path) -> Path:
    """Verify the pinned ZIP as a whole, then extract only the selected payload."""
    pin = AMONET_PINS[target]
    _safe_regular(archive)
    name = payload["payload"]
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise InstallerError(f"unsafe payload name in pin: {name!r}")
    if archive.stat().st_size != pin["archive_size"] or _sha256(archive) != pin["archive_sha256"]:
        raise InstallerError(f"Amonet ZIP does not match the pinned {pin['archive']}")
    member = f"amonet/bin/{name}"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / name
    temporary = destination.with_name(destination.name + ".part")
    try:
        with zipfile.ZipFile(archive) as bundle:
            if sum(1 for item in bundle.infolist() if item.filename == member) != 1:
                raise InstallerError(f"pinned ZIP must contain exactly one {member}")
            try:
                info = bundle.getinfo(member)
            except KeyError as error:
                raise InstallerError(f"pinned ZIP lacks {member}") from error
            if info.is_dir() or (info.external_attr >> 16) & 0o170000 not in (0, 0o100000):
                raise InstallerError(f"{member} is not a regular file in the pinned ZIP")
            if info.file_size != payload["size"]:
                raise InstallerError(f"{member} size differs from its pin")
            with bundle.open(info) as source, temporary.open("wb") as sink:
                shutil.copyfileobj(source, sink)
        if temporary.stat().st_size != payload["size"] or _sha256(temporary) != payload["sha256"]:
            raise InstallerError(f"extracted {member} does not match its pin")
    except (InstallerError, OSError, zipfile.BadZipFile):
        temporary.unlink(missing_ok=True)
        raise
    os.replace(temporary, destination)
    return destination


def brick_fastboot_payload(fastboot_bin: str, serial: str, payload: Path, budget: float) -> None:
    """Send the pinned fastbrick payload as upstream fastbrick.sh does.

    The payload is retried until the device drops off fastboot; a timed-out
    attempt is the expected success signal. eMMC read-only and device-mismatch
    responses are terminal and are never retried.
    """
    _safe_regular(payload)
    deadline = time.monotonic() + budget
    while True:
        try:
            result = subprocess.run(
                [fastboot_bin, "-s", serial, "flash", "brick", str(payload)],
                text=True, capture_output=True, timeout=8,
            )
        except subprocess.TimeoutExpired:
            print("FASTBOOT STAGE: brick payload accepted; device left fastboot (expected).", flush=True)
            return
        output = f"{result.stdout}\n{result.stderr}"
        if "eMMC-RO" in output:
            raise InstallerError("eMMC is permanently read-only (hardware failure); the device was not modified")
        if "Device mismatch" in output:
            raise InstallerError("brick payload rejected with Device mismatch; target or LK build is wrong")
        if result.returncode == 0:
            raise InstallerError("brick command succeeded but the device did not leave fastboot; not continuing")
        if time.monotonic() >= deadline:
            raise InstallerError("brick payload did not complete within the timeout")
        time.sleep(FASTBRICK_RETRY_SECONDS)


def target_asset_prefix(tag, target="radar_puffin"):
    if target not in TARGET_INDEX:
        raise InstallerError("unknown target")
    stem = tag if target == "radar_puffin" else TARGET_INDEX[target]["release_slug"] + "-" + tag.removeprefix("radar-puffin-")
    return "libreecho-" + stem


def verify_fastboot_product(fastboot_bin: str, serial: str, target=None, override=None) -> str:
    result = _run_command([fastboot_bin, "-s", serial, "getvar", "product"], 20, check=False)
    output = f"{result.stdout}\n{result.stderr}"
    match = re.search(r"product:\s*([A-Za-z0-9_-]+)\b", output, re.IGNORECASE)
    product = match.group(1).upper() if match else ""
    detected = next((t for t, d in TARGET_INDEX.items() if product in d["fastboot_products"]), None)
    if override is not None:
        if override not in TARGET_INDEX or (target is not None and target != override):
            raise InstallerError("invalid cross-flashed LK target override")
        if detected != override:
            print(f"WARNING: boot-chain identity differs from board: product={product or 'unknown'} explicit-target={override}", flush=True)
        return override
    if detected is None or (target is not None and detected != target):
        raise InstallerError("boot-chain product and release target mismatch; explicit --target required for cross-flashed LK")
    return detected


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


def _validate_userdata_partition_size(size: int) -> None:
    if size in USERDATA_SUPPORTED_BYTES:
        return
    expected = ", ".join(f"{value:#x}" for value in sorted(USERDATA_SUPPORTED_BYTES))
    raise InstallerError(
        f"userdata partition size mismatch: expected one of {expected}, got {size:#x}"
    )


def _userdata_free_block_map(metadata: str, expected_bytes: int) -> bytearray:
    """Parse C-locale dumpe2fs output, accepting only complete group free lists.

    The unindented filesystem-wide 'Free blocks:' field is a COUNT, not a
    block number. Per-group free counts, ranges and total coverage must agree.
    Host SEEK_HOLE/SEEK_DATA information is never a filesystem allocation map.
    """
    blocks, remainder = divmod(expected_bytes, 4096)
    if remainder or blocks < 1:
        raise InstallerError("invalid userdata block geometry")

    def header(name: str) -> int:
        matches = re.findall(r"^" + re.escape(name) + r":[ \t]+([0-9]+)[ \t]*$", metadata, re.M)
        if len(matches) != 1:
            raise InstallerError(f"dumpe2fs missing or duplicate {name!r} header")
        return int(matches[0])

    if header("Block size") != 4096 or header("Block count") != blocks or header("First block") != 0:
        raise InstallerError("dumpe2fs geometry does not match userdata")
    expected_free = header("Free blocks")
    if not 0 < expected_free < blocks:
        raise InstallerError("dumpe2fs reported an invalid free-block count")
    free = bytearray(blocks)
    group = None
    next_group = 0
    next_block = 0
    group_free = None
    list_seen = False

    def finish_group() -> None:
        if group is not None:
            if group_free is None or not list_seen:
                raise InstallerError("dumpe2fs omitted a userdata block-group free list")
            start, end = group
            if sum(free[start:end + 1]) != group_free:
                raise InstallerError("dumpe2fs block-group free count mismatch")

    for line in metadata.splitlines():
        match = re.match(r"^Group ([0-9]+): \(Blocks ([0-9]+)-([0-9]+)\)", line)
        if line.startswith("Group "):
            if match is None:
                raise InstallerError("dumpe2fs reported an invalid block-group header")
            finish_group()
            number, start, end = map(int, match.groups())
            if number != next_group or start != next_block or not start <= end < blocks:
                raise InstallerError("dumpe2fs block-group coverage is inconsistent")
            group = (start, end)
            next_group += 1
            next_block = end + 1
            group_free = None
            list_seen = False
            continue
        if group is None:
            continue
        count = re.match(r"^\s+([0-9]+) free blocks,", line)
        if count:
            if group_free is not None:
                raise InstallerError("dumpe2fs repeated a block-group free count")
            group_free = int(count[1])
        # Only the INDENTED per-group field is an allocation range list.
        ranges = re.fullmatch(r"[ \t]+Free blocks:[ \t]*(.*)", line)
        if ranges is None:
            continue
        if list_seen:
            raise InstallerError("dumpe2fs repeated a block-group free list")
        list_seen = True
        previous = group[0] - 1
        if not ranges[1].strip():
            continue
        for item in ranges[1].split(","):
            bounds = re.fullmatch(r"([0-9]+)(?:-([0-9]+))?", item.strip())
            if bounds is None:
                raise InstallerError("dumpe2fs reported an invalid free-block range")
            start = int(bounds[1])
            end = int(bounds[2] or bounds[1])
            if not group[0] <= start <= end <= group[1] or start <= previous:
                raise InstallerError("dumpe2fs free-block range is overlapping or outside its group")
            free[start:end + 1] = b"\1" * (end - start + 1)
            previous = end
    finish_group()
    if next_block != blocks or sum(free) != expected_free or free[0]:
        raise InstallerError("dumpe2fs free-block map is incomplete or inconsistent")
    return free

def _write_userdata_sparse(raw: Path, sparse: Path, expected_bytes: int, metadata: str) -> int:
    """Write Android RAW/DONT_CARE chunks from ext4 allocation, not host holes.

    Every allocated block is copied byte-for-byte, INCLUDING zeroed inode
    tables, bitmaps, journal and directory padding. Only ext4-free blocks are
    skipped, so stale bytes on the target cannot become live metadata.
    """
    if raw.stat().st_size != expected_bytes:
        raise InstallerError("generated userdata filesystem has the wrong raw size")
    free = _userdata_free_block_map(metadata, expected_bytes)
    programmed_bytes = (len(free) - sum(free)) * 4096
    if programmed_bytes > 64 * 1024 * 1024:
        raise InstallerError(
            f"userdata allocated blocks exceed LK write budget: {programmed_bytes} bytes; "
            "limit=67108864; refusing to flash"
        )
    runs = []
    start = 0
    while start < len(free):
        end = start + 1
        while end < len(free) and free[end] == free[start]:
            end += 1
        runs.append((start, end, bool(free[start])))
        start = end
    # Exclusive creation prevents accidental reuse of an older output image.
    with raw.open("rb") as source, sparse.open("xb") as output:
        output.write(struct.pack("<IHHHHIIII", 0xED26FF3A, 1, 0, 28, 12,
                                 4096, len(free), len(runs), 0))
        for start, end, unused in runs:
            length = (end - start) * 4096
            output.write(struct.pack("<HHII", 0xCAC3 if unused else 0xCAC1, 0,
                                     end - start, 12 if unused else 12 + length))
            if unused:
                continue
            source.seek(start * 4096)
            while length:
                data = source.read(min(length, 1024 * 1024))
                if not data:
                    raise InstallerError("userdata image truncated while copying allocated blocks")
                output.write(data)
                length -= len(data)
        output.flush()
        os.fsync(output.fileno())
    return programmed_bytes

def format_userdata_in_fastboot(fastboot_bin: str, serial: str, timeout: float) -> None:
    """Build and flash a compatible sparse ext4 filesystem to userdata."""
    print("FASTBOOT STAGE: validating target product and partition geometry.", flush=True)
    verify_fastboot_product(fastboot_bin, serial)
    size = _fastboot_partition_size(fastboot_bin, serial, "userdata")
    _validate_userdata_partition_size(size)
    print(
        f"FASTBOOT STAGE: formatting only userdata as ext4 ({size} bytes); "
        "boot, system, persist, and expdb are not being formatted.",
        flush=True,
    )
    tool_root = Path(fastboot_bin).resolve().parent
    mke2fs = tool_root / "mke2fs"
    dumpe2fs = tool_root / "dumpe2fs"
    if not (mke2fs.is_file() and dumpe2fs.is_file()):
        raise InstallerError("staged userdata image tools disappeared after host preflight")
    with tempfile.TemporaryDirectory(prefix="userdata-image-", dir=tool_root) as temporary:
        root = Path(temporary)
        raw = root / "userdata.ext4"
        sparse = root / "userdata.sparse.img"
        with raw.open("wb") as stream:
            stream.truncate(size)
        _run_command(
            [str(mke2fs), "-F", "-t", "ext4", "-b", "4096", "-L", "LIBREECHO_DATA",
             "-m", "0", "-O", "^64bit,^metadata_csum,^metadata_csum_seed,^orphan_file",
             "-E", "lazy_itable_init=0,lazy_journal_init=0", str(raw)],
            max(timeout, 300),
        )
        # env affects only this child process, not the caller's global locale.
        metadata = _run_command(
            ["env", "LC_ALL=C", str(dumpe2fs), str(raw)], max(timeout, 300)
        ).stdout
        expected_programmed = _write_userdata_sparse(raw, sparse, size, metadata)
        programmed_bytes = _validate_android_sparse_image(sparse, size)
        if programmed_bytes != expected_programmed:
            raise InstallerError("userdata sparse write accounting mismatch")
        print(
            f"FASTBOOT STAGE: generated validated sparse ext4 image ({sparse.stat().st_size} bytes); "
            f"LK will program only {programmed_bytes} bytes and skip {size - programmed_bytes} "
            "ext4-free bytes. Flashing only userdata.",
            flush=True,
        )
        _run_command_with_heartbeat(
            [fastboot_bin, "-s", serial, "flash", "userdata", str(sparse)],
            max(timeout, USERDATA_FLASH_TIMEOUT),
            "FASTBOOT STAGE: userdata write still in progress; do not disconnect USB",
        )
    print("FASTBOOT STAGE: userdata filesystem format complete.", flush=True)


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


def _capture_evidence_command(argv: list[str], destination: Path, timeout: float = 20) -> None:
    """Capture a best-effort diagnostic command without masking the install error."""
    try:
        result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
        destination.write_text(
            f"$ {' '.join(argv)}\nreturncode={result.returncode}\n\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n",
            encoding="utf-8",
        )
        _append_log(f"FAILURE_EVIDENCE {destination.name} rc={result.returncode}")
    except (OSError, subprocess.TimeoutExpired) as error:
        destination.write_text(
            f"$ {' '.join(argv)}\ncollection failed: {type(error).__name__}: {error}\n",
            encoding="utf-8",
        )
        _append_log(f"FAILURE_EVIDENCE {destination.name} collection failed: {error}")


def collect_failure_evidence(args: argparse.Namespace, reason: str) -> Path | None:
    """Collect available host/device evidence and package it into one archive."""
    parent = ACTIVE_LOG_PATH.parent if ACTIVE_LOG_PATH is not None else Path.cwd()
    archive_path = parent / "libreecho-installer-evidence.tar.gz"
    try:
        with tempfile.TemporaryDirectory(prefix="libreecho-evidence-", dir=parent) as temporary:
            root = Path(temporary)
            (root / "failure.txt").write_text(f"reason={reason}\nargv={sys.argv!r}\n", encoding="utf-8")

            host_commands = (
                ("host-uname.txt", ["uname", "-a"]),
                ("host-id.txt", ["id"]),
                ("host-usb.txt", ["lsusb"]),
                ("host-serial-devices.txt", ["sh", "-c", "ls -l /dev/ttyACM* /dev/ttyUSB* 2>&1"]),
                ("host-dmesg-usb.txt", ["sh", "-c", "dmesg | tail -200"]),
                ("host-modules.txt", ["lsmod"]),
                ("host-processes.txt", ["ps", "-ef"]),
            )
            for filename, command in host_commands:
                if shutil.which(command[0]) is not None:
                    _capture_evidence_command(command, root / filename)

            fastboot = str(getattr(args, "fastboot_bin", "fastboot"))
            fastboot_serials: list[str] = []
            if shutil.which(fastboot) is not None or Path(fastboot).is_file():
                devices = root / "fastboot-devices.txt"
                _capture_evidence_command([fastboot, "devices"], devices)
                try:
                    text = devices.read_text(encoding="utf-8")
                    fastboot_serials = [line.split()[0] for line in text.splitlines() if len(line.split()) >= 2 and line.split()[1] == "fastboot"]
                except OSError:
                    pass
                requested = getattr(args, "fastboot_serial", "auto")
                if requested != "auto" and requested not in fastboot_serials:
                    fastboot_serials.append(requested)
                for serial in dict.fromkeys(fastboot_serials):
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", serial)
                    _capture_evidence_command([fastboot, "-s", serial, "getvar", "all"], root / f"fastboot-{safe}-getvar-all.txt")

            adb = str(getattr(args, "adb_bin", "adb"))
            adb_serials: list[str] = []
            if shutil.which(adb) is not None or Path(adb).is_file():
                devices = root / "adb-devices.txt"
                _capture_evidence_command([adb, "devices", "-l"], devices)
                try:
                    adb_serials = [line.split()[0] for line in devices.read_text(encoding="utf-8").splitlines() if len(line.split()) >= 2 and line.split()[1] == "device"]
                except OSError:
                    pass
                for serial in dict.fromkeys(adb_serials):
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", serial)
                    for name, remote in (("props", ["getprop"]), ("mounts", ["cat", "/proc/mounts"]), ("partitions", ["cat", "/proc/partitions"]), ("cmdline", ["cat", "/proc/cmdline"]), ("dmesg", ["dmesg"])):
                        _capture_evidence_command([adb, "-s", serial, "shell", *remote], root / f"adb-{safe}-{name}.txt", timeout=30)

            cache_root = Path(getattr(args, "cache_root", ""))
            if cache_root.is_dir():
                for amonet_log in cache_root.glob("**/modules/amonet.log"):
                    if amonet_log.is_file() and amonet_log.stat().st_size <= 2 * 1024 * 1024:
                        shutil.copy2(amonet_log, root / f"amonet-{amonet_log.parent.parent.name}.log")

            if ACTIVE_LOG_PATH is not None and ACTIVE_LOG_PATH.is_file():
                shutil.copy2(ACTIVE_LOG_PATH, root / "libreecho-installer.log")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(root, arcname="libreecho-installer-evidence")
        os.chmod(archive_path, 0o600)
        _append_log(f"FAILURE_EVIDENCE archive={archive_path}")
        return archive_path
    except (OSError, tarfile.TarError) as error:
        _append_log(f"FAILURE_EVIDENCE archive creation failed: {error}")
        return None


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
            or manifest["board"] not in TARGET_INDEX or manifest["soc"] != "mt8163"
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
    amonet = _exact_keys(manifest["amonet"], {"archive", "archive_sha256", "archive_size"}, "amonet record")
    pin = AMONET_PINS[manifest["board"]]
    if amonet != {"archive": pin["archive"], "archive_sha256": pin["archive_sha256"], "archive_size": pin["archive_size"]}:
        raise InstallerError("manifest Amonet record does not match the pinned ZIP for this target")
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


def _read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallerError("malformed installer state") from error
    if (not isinstance(value, dict) or not {"phase", "release", "bundle_sha256"}.issubset(value)
            or set(value) - {"phase", "release", "bundle_sha256", "userdata_formatted", "device_serial", "slots", "board"}
            or not isinstance(value["phase"], str)
            or value["phase"] not in ONE_SHOT_PHASES or not isinstance(value["release"], str)
            or not RELEASE.fullmatch(value["release"])
            or not isinstance(value["bundle_sha256"], str) or not SHA256.fullmatch(value["bundle_sha256"])
            or ("userdata_formatted" in value and not isinstance(value["userdata_formatted"], bool))
            or ("device_serial" in value and (not isinstance(value["device_serial"], str)
                or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value["device_serial"])))
            or ("slots" in value and (not isinstance(value["slots"], str)
                or value["slots"] not in {"a", "b", "both"}))):
        raise InstallerError("malformed installer state")
    if "board" in value and value["board"] not in TARGET_INDEX:
        raise InstallerError("malformed installer state target")
    return value


def _write_state(path: Path, state: dict[str, Any]) -> None:
    # Keep device/slot binding and format evidence only within this exact bundle.
    # Starting a fresh installation explicitly resets the prior transaction.
    state = dict(state)
    if state.get("phase") != "RELEASE_READY" and path.exists():
        try:
            previous = _read_state(path)
        except InstallerError:
            previous = {}
        if (previous.get("release") == state.get("release")
                and previous.get("bundle_sha256") == state.get("bundle_sha256")):
            for key in ("userdata_formatted", "device_serial", "slots", "board"):
                if key not in state and key in previous:
                    state[key] = previous[key]
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


def _target_metadata_assets(release_dir: Path, prefix: str, release_tag: str, plan: dict, inventory: dict) -> set[str]:
    """Standalone host-side v3 closure; distribution has no Product modules."""
    features = ('airplay2', 'tts', 'wakeword', 'stt', 'assistant')
    fields = ('asset', 'size', 'sha256', 'manifest_asset', 'manifest_size', 'manifest_sha256', 'daemon_path', 'daemon_sha256')
    top = ('format', 'manifest_version', 'board', 'soc', 'architecture', 'image_profile', 'transaction_type', 'transaction_id', 'release', 'version', 'update_channel', 'service_profile', 'minimum_updater_schema', 'commit_policy', 'boot_filename', 'boot_size', 'boot_sha256', 'feature_ids')
    expected_keys = set(top) - {'feature_ids'} | {'schema', 'features', 'config_schema'}
    target = 'biscuit' if prefix.startswith('libreecho-biscuit-') else 'radar_puffin'
    slug = TARGET_INDEX[target]['release_slug']
    fixed = {'schema': 'libreecho-product-target-plan-v3', 'format': 'libreecho-ota-v3', 'manifest_version': 1, 'board': target, 'soc': 'mt8163', 'architecture': 'armv7', 'image_profile': 'ota', 'transaction_type': 'system', 'release': release_tag, 'service_profile': 'production', 'commit_policy': 'after-slot-confirm', 'minimum_updater_schema': 3, 'boot_filename': 'boot.img', 'boot_size': 16777216}
    if set(plan) != expected_keys or any(type(plan.get(k)) is not type(v) or plan.get(k) != v for k, v in fixed.items()):
        raise InstallerError('invalid immutable v3 target plan')
    if (not VERSION.fullmatch(str(plan['version'])) or plan['update_channel'] not in ('dev', 'stable')
            or type(plan['config_schema']) is not int or not 1 <= plan['config_schema'] < 2**31
            or not re.fullmatch(r'[0-9a-f]{64}', str(plan['boot_sha256']))):
        raise InstallerError('invalid v3 version, boot or config schema')
    records = plan['features']
    if not isinstance(records, list) or [r.get('feature_id') for r in records if isinstance(r, dict)] != list(features):
        raise InstallerError('v3 target must contain all five features')
    material = {k: v for k, v in plan.items() if k != 'transaction_id'}
    if plan['transaction_id'] != 'txn-' + hashlib.sha256(json.dumps(material, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]:
        raise InstallerError('v3 transaction identity mismatch')
    assets = []
    daemon_names = ('libreecho-audio-engine', 'libreecho-ttsd', 'libreecho-waked', 'libreecho-sttd', 'libreecho-agentd')
    for record, daemon in zip(records, daemon_names):
        if (set(record) != set(fields) | {'feature_id'} or record['daemon_path'] != 'usr/local/sbin/' + daemon
                or not re.fullmatch(r'[0-9a-f]{64}', str(record['daemon_sha256']))):
            raise InstallerError('invalid v3 feature fields or daemon')
        fid = record['feature_id']
        for kind, stem, suffix in (('payload', '', 'payload.squashfs'), ('manifest', 'manifest_', 'manifest.json')):
            name, sha, size = (record[stem + key] for key in ('asset', 'sha256', 'size'))
            if (not re.fullmatch(r'[0-9a-f]{64}', str(sha)) or type(size) is not int or not 0 < size < 2**63
                    or name != f'libreecho-{slug}-base-{fid}-{sha}.{suffix}'):
                raise InstallerError('v3 asset is not content-addressed')
            path = release_dir / name
            _safe_regular(path)
            if path.stat().st_size != size or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise InstallerError('v3 target asset identity mismatch')
            assets.append(dict(feature_id=fid, kind=kind, name=name, size=size, sha256=sha))
    assets.sort(key=lambda r: r['name'])
    if inventory != dict(schema='libreecho-product-target-assets-v3', board=target, release=release_tag, assets=assets):
        raise InstallerError('v3 inventory differs from target')
    completeness_path = release_dir / f'libreecho-{slug}-release-completeness.json'
    _safe_regular(completeness_path)
    completeness = json.loads(completeness_path.read_text())
    refs = [{**a, 'role': 'target', 'source_release': release_tag, 'source_asset': a['name']} for a in assets]
    if completeness != dict(schema='libreecho-release-completeness-v1', target=target, references=refs):
        raise InstallerError('v3 completeness is not owned by this release')
    # Pin the OTA and recovery route to the same exact manifest serialization.
    ordered = [(k, ','.join(features) if k == 'feature_ids' else plan[k]) for k in top]
    for record in records:
        ordered.extend((f'feature_{record["feature_id"]}_{k}', record[k]) for k in fields)
    ordered.append(('config_schema', plan['config_schema']))
    raw = ''.join(f'{k}={v}\n' for k, v in ordered).encode('ascii')
    ota = release_dir / f'{prefix}.ota.tar'
    _safe_regular(ota)
    with tarfile.open(ota, 'r:') as archive:
        members = archive.getmembers()
        if [m.name for m in members] != ['manifest', 'manifest.sig', 'boot.img'] or any(not m.isfile() for m in members) or members[0].size > 65536:
            raise InstallerError('invalid v3 OTA control member set')
        if archive.extractfile('manifest').read() != raw:
            raise InstallerError('v3 OTA differs from recovery target')
    names = {f'{prefix}-feature-plan.json', f'{prefix}-feature-assets.json', completeness_path.name, *(a['name'] for a in assets)}
    names.update(f'{prefix}-{fid}.{suffix}' for fid in features for suffix in ('squashfs', 'manifest.json'))
    if target == 'radar_puffin':
        names.update({'libreecho-install.zip', 'bundle.manifest'})
    return names


def _v2_metadata_assets(release_dir: Path, prefix: str, release_tag: str) -> set[str]:
    """Checksum-covered OTA v2 metadata and signed replacement asset names.

    A published OTA v2 release adds the signed feature asset inventory, its
    binding feature plan, and every feature replacement asset to the release's
    ``SHA256SUMS`` beyond the initial-install set. The inventory is the
    authoritative list of those extra names, so validate it here and allow only
    what it names rather than accepting arbitrary checksum-covered files.
    Releases without the inventory contribute nothing.

    Stable tags name the numeric version, so the inventory must repeat it.
    Development and nightly tags name a product commit instead, so the
    inventory's own version is bound to the published plan and to the
    ``libreecho-radar-puffin-<version>-`` namespace every replacement asset
    must use.
    """
    inventory_path = release_dir / f"{prefix}-feature-assets.json"
    if not inventory_path.exists():
        return set()
    _safe_regular(inventory_path)
    plan_path = release_dir / f"{prefix}-feature-plan.json"
    _safe_regular(plan_path)
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InstallerError("published feature asset inventory is unreadable") from error
    if isinstance(inventory, dict) and inventory.get('schema') == 'libreecho-product-target-assets-v3':
        return _target_metadata_assets(release_dir, prefix, release_tag, plan, inventory)
    if (not isinstance(inventory, dict)
            or inventory.get("schema") != "libreecho-product-feature-assets-v1"
            or inventory.get("transaction_type") != "system"
            or inventory.get("activation") != "reboot"):
        raise InstallerError("published feature asset inventory is invalid")
    version = inventory.get("release")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise InstallerError("published feature asset inventory is invalid")
    if (not isinstance(plan, dict)
            or plan.get("schema") != "libreecho-product-feature-plan-v1"
            or plan.get("transaction_type") != "system"
            or plan.get("activation") != "reboot"
            or plan.get("release") != version):
        raise InstallerError("published feature plan does not match the inventory")
    if release_tag.startswith("radar-puffin-v"):
        if release_tag.removeprefix("radar-puffin-v") != version:
            raise InstallerError("published feature asset inventory is invalid")
    elif not release_tag.startswith(("radar-puffin-build-", "radar-puffin-nightly-")):
        raise InstallerError("published feature asset inventory needs a stable or dev release")
    assets = inventory.get("assets")
    if not isinstance(assets, list):
        raise InstallerError("published feature asset inventory is malformed")
    slug = "biscuit" if prefix.startswith("libreecho-biscuit-") else "radar-puffin"
    target = "biscuit" if slug == "biscuit" else "radar_puffin"
    if plan.get("board", "radar_puffin") != target or inventory.get("board", "radar_puffin") != target:
        raise InstallerError("published feature target mismatch")
    namespace = f"libreecho-{slug}-{version}-"
    names = {plan_path.name, inventory_path.name}
    for item in assets:
        name = item.get("name") if isinstance(item, dict) else None
        if (not isinstance(name, str) or not name
                or Path(name).name != name or not PUBLIC_NAME.fullmatch(name)
                or not name.startswith(namespace)):
            raise InstallerError("published feature asset inventory is malformed")
        names.add(name)
    completeness_path = release_dir / f'libreecho-{slug}-release-completeness.json'
    if completeness_path.exists():
        _safe_regular(completeness_path)
        completeness = json.loads(completeness_path.read_text(encoding='utf-8'))
        if completeness.get('schema') != 'libreecho-release-completeness-v1' or not isinstance(completeness.get('references'), list):
            raise InstallerError('published release completeness provenance is malformed')
        wanted = {}
        for record in plan.get('features', []):
            fid = record['feature_id']
            for kind in ('payload', 'manifest'):
                wanted[(fid, 'base', kind)] = record[f'base_{kind}_sha256']
                if record['action'] != 'preserve':
                    wanted[(fid, 'target', kind)] = record['sha256' if kind == 'payload' else 'manifest_sha256']
        seen = set()
        for item in completeness['references']:
            key = (item.get('feature_id'), item.get('role'), item.get('kind'))
            name = item.get('name')
            if key not in wanted or key in seen or item.get('sha256') != wanted[key]:
                raise InstallerError('completeness provenance does not match feature plan')
            if not isinstance(name, str) or Path(name).name != name or not PUBLIC_NAME.fullmatch(name):
                raise InstallerError('unsafe completeness asset name')
            if item['role'] == 'base':
                suffix = 'payload.squashfs' if item['kind'] == 'payload' else 'manifest.json'
                expected_name = f"libreecho-{slug}-base-{item['feature_id']}-{item['sha256']}.{suffix}"
                if name != expected_name:
                    raise InstallerError('completeness base namespace mismatch')
            elif name not in names:
                raise InstallerError('completeness target is absent from OTA inventory')
            seen.add(key)
            names.add(name)
        if seen != set(wanted):
            raise InstallerError('incomplete release base provenance')
        names.add(completeness_path.name)
        if target == 'radar_puffin':
            # The legacy unqualified aliases exist for Radar only.
            names.update({'libreecho-install.zip', 'bundle.manifest'})
        # Candidate assets stay published under their old names even when the
        # install tar selects the signed preserved base instead of the candidate.
        names.update(f'{prefix}-{fid}.{suffix}' for fid in ('airplay2', 'tts', 'wakeword', 'stt', 'assistant')
                     for suffix in ('squashfs', 'manifest.json'))
    return names


def _prepare(release_dir: Path, cache_root: Path, release_tag: str, target: str = "radar_puffin") -> tuple[dict[str, Any], Path]:
    if not RELEASE.fullmatch(release_tag):
        raise InstallerError("invalid release tag")
    if not release_dir.is_dir() or release_dir.is_symlink():
        raise InstallerError("unsafe release directory")
    prefix = target_asset_prefix(release_tag, target)
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
    if manifest["board"] != target:
        raise InstallerError("install manifest and selected target mismatch")
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
        f"libreecho-{TARGET_INDEX[target]['release_slug']}-dev.ota.tar",
        f"{prefix}-build.json",
        f"{prefix}-run-one-shot.sh",
    }
    if release_tag.startswith(("radar-puffin-nightly-", "radar-puffin-build-")):
        optional.update({f"{prefix}-build.json", f"{prefix}-verification.txt", f"{prefix}-run-one-shot.sh"})
    if release_tag.startswith("radar-puffin-v"):
        optional.add(f"libreecho-{TARGET_INDEX[target]['release_slug']}-stable.ota.tar")
    optional |= _v2_metadata_assets(release_dir, prefix, release_tag)
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
    # Continuation must revalidate the same complete release inventory, including
    # optional checksum-covered metadata, even after a local source disappears.
    if release_dir.resolve() != downloads.resolve():
        for name in records:
            _copy_atomic(release_dir / name, downloads / name)
        _copy_atomic(checksums, downloads / checksums.name)
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
    amonet_zip: Path | str | None,
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
    brick_timeout: float = 120,
    fastboot_timeout: float = 120,
    adb_timeout: float = 180,
    open_browser: bool = True,
    execute_hardware: bool = False,
    install_host_deps: bool = False,
    emulator_root: Path | str | None = None,
    emulator_kernel: Path | str | None = None,
    emulator_initramfs: Path | str | None = None,
    target: str | None = None,
) -> dict[str, str]:
    """Unlock with the pinned fastbrick payload, install boot payloads, and open first-boot setup."""
    if not execute_hardware:
        raise InstallerError("one-shot requires --execute-hardware")
    if slots not in {"a", "b", "both"}:
        raise InstallerError("slots must be a, b, or both")
    adb_forward_command(adb_bin, fastboot_serial, local_port)
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
        require_host_commands(fastboot_bin, adb_bin)
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
        target_override = target
        if target is None:
            devices = fastboot_devices(fastboot_bin)
            if len(devices) != 1:
                raise InstallerError("no unambiguous stock fastboot identity available; specify --target for BROM/cross-flashed LK")
            target = verify_fastboot_product(fastboot_bin, select_fastboot_serial(fastboot_bin, fastboot_serial))
        if target not in TARGET_INDEX:
            raise InstallerError("unknown target")
        if release_dir is None:
            release_dir = download_release(release_tag, release_repository, download_root, target)
        else:
            release_dir = Path(release_dir)
        manifest, bundle = _prepare(release_dir, cache_root, release_tag, target)
        boot = cache_root / release_tag / "bundle" / manifest["boot"]["name"]
        validate_public_boot_image(boot, manifest["boot"]["sha256"])
        state_path = _state_path(state_root, install_id)
        bundle_sha = _sha256(bundle)
        _write_state(state_path, {"phase": "RELEASE_READY", "board": target, "release": release_tag, "bundle_sha256": bundle_sha, "userdata_formatted": False})
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
            _run_command([str(tool)], brick_timeout)
        else:
            serial = select_fastboot_serial(fastboot_bin, fastboot_serial)
            verify_fastboot_product(fastboot_bin, serial, target, target_override)
            plan = plan_fastbrick(fastboot_bin, serial, target, amonet_zip, cache_root)
            if plan is not None:
                lk_build, staged = plan
                _write_state(state_path, {"phase": "AMONET_VERIFIED", "release": release_tag, "bundle_sha256": bundle_sha})
                print(f"FASTBOOT STAGE: sending pinned fastbrick payload for LK build {lk_build}.", flush=True)
                brick_fastboot_payload(fastboot_bin, serial, staged, brick_timeout)
                confirm_post_brick_identity(fastboot_bin, serial, wait_for_fastboot_serial(fastboot_bin, fastboot_serial, fastboot_timeout), target)
        _write_state(state_path, {"phase": "AMONET_HANDOFF", "release": release_tag, "bundle_sha256": bundle_sha})
        if slots not in {"a", "b", "both"}:
            raise InstallerError("slots must be a, b, or both")
        selected = ("a", "b") if slots == "both" else (slots,)
        print("FASTBOOT STAGE: waiting for the unlocked fastboot device.", flush=True)
        serial = wait_for_fastboot_serial(fastboot_bin, fastboot_serial, fastboot_timeout)
        print(f"FASTBOOT STAGE: detected device {serial}; starting validated fastboot operations.", flush=True)
        _write_state(state_path, {"phase": "AMONET_HANDOFF", "release": release_tag,
                                  "bundle_sha256": bundle_sha, "userdata_formatted": False,
                                  "device_serial": serial, "slots": slots})
        verify_fastboot_product(fastboot_bin, serial, target, target_override)
        format_userdata_in_fastboot(fastboot_bin, serial, fastboot_timeout)
        _write_state(state_path, {"phase": "AMONET_HANDOFF", "release": release_tag, "bundle_sha256": bundle_sha, "userdata_formatted": True})
        print("FASTBOOT STAGE: verifying boot payload partition geometry.", flush=True)
        _verify_fastboot_payload_geometry(fastboot_bin, serial)
        _write_state(state_path, {"phase": "FASTBOOT_READY", "release": release_tag, "bundle_sha256": bundle_sha})
        for slot in selected:
            print(f"FASTBOOT STAGE: flashing verified boot payload to boot_{slot}.", flush=True)
            _run_command([fastboot_bin, "-s", serial, "flash", f"boot_{slot}", str(boot)], fastboot_timeout)
        _write_state(state_path, {"phase": "BOOT_WRITTEN", "release": release_tag, "bundle_sha256": bundle_sha})
        print("FASTBOOT STAGE: rebooting into the installed LibreEcho boot image.", flush=True)
        try:
            subprocess.run([fastboot_bin, "-s", serial, "reboot"], text=True, capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            print("Fastboot reboot did not acknowledge; waiting for ADB anyway.", flush=True)
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
    target: str | None = None,
) -> dict[str, str]:
    if not execute_hardware:
        raise InstallerError("continuation requires --execute-hardware")
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    with (cache_root / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise InstallerError("another installer is already running") from error
        state_path = _state_path(Path(state_root), install_id)
        state = _read_state(state_path)
        if not isinstance(release_tag, str) or not RELEASE.fullmatch(release_tag):
            raise InstallerError("invalid continuation release tag")
        if state["release"] != release_tag:
            raise InstallerError(
                f"continuation release tag does not match saved state: {release_tag} != {state['release']}"
            )
        if state["phase"] not in {"AMONET_HANDOFF", "FASTBOOT_READY", "BOOT_WRITTEN", "ADB_READY",
                                  "READBACK_VERIFIED", "FEATURES_STAGED", "WEBUI_FORWARDED"}:
            raise InstallerError(f"cannot continue before Amonet handoff: {state['phase']}")
        bound_serial = state.get("device_serial")
        if bound_serial:
            if fastboot_serial not in {"auto", bound_serial}:
                raise InstallerError("requested device does not match the saved installation")
            fastboot_serial = bound_serial
        elif fastboot_serial == "auto":
            raise InstallerError("legacy state has no device binding; specify the original --fastboot-serial")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", fastboot_serial):
            raise InstallerError("invalid continuation device serial")
        if "slots" in state and state["slots"] != slots:
            raise InstallerError("requested slots do not match the saved installation")
        adb_forward_command(adb_bin, fastboot_serial, local_port)
        release = state["release"]
        cache_root = Path(cache_root)
        release_sources = cache_root / "downloads" / release
        if not release_sources.is_dir():
            release_sources = cache_root / release / "downloads"
        saved_target = state.get("board", "radar_puffin")
        if target is not None and target != saved_target:
            raise InstallerError("continuation target differs from saved installation")
        manifest, bundle = _prepare(release_sources, cache_root, release, saved_target)
        if _sha256(bundle) != state["bundle_sha256"]:
            raise InstallerError("cached bundle hash changed since Amonet handoff")
        boot = cache_root / release / "bundle" / manifest["boot"]["name"]
        validate_public_boot_image(boot, manifest["boot"]["sha256"])
        if slots not in {"a", "b", "both"}:
            raise InstallerError("slots must be a, b, or both")
        selected = ("a", "b") if slots == "both" else (slots,)
        phase = state["phase"]
        userdata_formatted = state.get("userdata_formatted", False)
        staged = phase in {"FEATURES_STAGED", "WEBUI_FORWARDED"}
        if staged and not userdata_formatted:
            raise InstallerError("completed staging lacks userdata-format evidence; refusing destructive repair")
        needs_repair = phase in {"BOOT_WRITTEN", "ADB_READY", "READBACK_VERIFIED"} and not userdata_formatted
        require_host_commands(adb_bin)
        # `BOOT_WRITTEN` is saved immediately before `fastboot reboot`. If the
        # installer exited before that reboot completed, the device is still in
        # fastboot and an ADB-only resume can never recover it. Probe for a
        # still-present fastboot device so the pending reboot is issued before
        # falling through to the ADB path; when fastboot is unavailable, keep the
        # existing ADB-only behaviour.
        fastboot_waiting = False
        if (phase == "BOOT_WRITTEN" and userdata_formatted
                and (shutil.which(fastboot_bin) is not None or Path(fastboot_bin).is_file())):
            try:
                fastboot_serials_present = fastboot_devices(fastboot_bin)
            except InstallerError:
                fastboot_serials_present = []
            # Only the requested target counts as the pending reboot. An
            # unrelated device sitting in fastboot must not divert this resume
            # into `select_fastboot_serial`, which would then fail for the saved
            # serial even though the saved device is already reachable in ADB.
            if fastboot_serial == "auto":
                fastboot_waiting = len(fastboot_serials_present) == 1
            else:
                fastboot_waiting = fastboot_serial in fastboot_serials_present
        if (phase in {"AMONET_HANDOFF", "FASTBOOT_READY"}
                or (needs_repair and repair_userdata) or fastboot_waiting):
            require_host_commands("bash", fastboot_bin)
            fastboot_bin = prepare_fastboot_tools(
                fastboot_bin, cache_root, install_host_deps=install_host_deps
            )
        if needs_repair:
            if not repair_userdata:
                raise InstallerError(
                    "saved run reached ADB before userdata was formatted; "
                    "rerun continue-one-shot with --repair-userdata to perform "
                    "the explicit fastboot userdata format before feature staging"
                )
            serial = select_adb_serial(adb_bin, fastboot_serial)
            _write_state(state_path, {**state, "device_serial": serial, "slots": slots})
            print(
                f"RECOVERY STAGE: exact ADB device {serial} selected; "
                "rebooting to fastboot to repair userdata.",
                flush=True,
            )
            _run_command([adb_bin, "-s", serial, "reboot", "bootloader"], 20)
            print("FASTBOOT STAGE: waiting for the repaired device.", flush=True)
            fastboot = wait_for_fastboot_serial(fastboot_bin, fastboot_serial, fastboot_timeout)
            print(f"FASTBOOT STAGE: detected device {fastboot}; formatting userdata.", flush=True)
            verify_fastboot_product(fastboot_bin, fastboot, saved_target, target)
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
        elif phase in {"AMONET_HANDOFF", "FASTBOOT_READY"}:
            print("FASTBOOT STAGE: waiting for the unlocked fastboot device.", flush=True)
            serial = wait_for_fastboot_serial(fastboot_bin, fastboot_serial, fastboot_timeout)
            print(f"FASTBOOT STAGE: detected device {serial}; starting validated fastboot operations.", flush=True)
            _write_state(state_path, {**state, "device_serial": serial, "slots": slots})
            verify_fastboot_product(fastboot_bin, serial, saved_target, target)
            if not userdata_formatted:
                if phase == "FASTBOOT_READY" and not repair_userdata:
                    raise InstallerError("FASTBOOT_READY lacks userdata-format evidence; explicit --repair-userdata is required")
                format_userdata_in_fastboot(fastboot_bin, serial, fastboot_timeout)
            _write_state(state_path, {"phase": "AMONET_HANDOFF", "release": release, "bundle_sha256": state["bundle_sha256"], "userdata_formatted": True})
            print("FASTBOOT STAGE: verifying boot payload partition geometry.", flush=True)
            _verify_fastboot_payload_geometry(fastboot_bin, serial)
            _write_state(state_path, {"phase": "FASTBOOT_READY", "release": release, "bundle_sha256": state["bundle_sha256"]})
            for slot in selected:
                print(f"FASTBOOT STAGE: flashing verified boot payload to boot_{slot}.", flush=True)
                _run_command([fastboot_bin, "-s", serial, "flash", f"boot_{slot}", str(boot)], fastboot_timeout)
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
            if fastboot_waiting:
                print(
                    "FASTBOOT STAGE: device is still in fastboot after BOOT_WRITTEN; "
                    "issuing the pending reboot.",
                    flush=True,
                )
                pending_serial = select_fastboot_serial(fastboot_bin, fastboot_serial)
                try:
                    subprocess.run(
                        [fastboot_bin, "-s", pending_serial, "reboot"],
                        text=True, capture_output=True, timeout=20,
                    )
                except subprocess.TimeoutExpired:
                    print("Fastboot reboot did not acknowledge; waiting for ADB anyway.", flush=True)
                print("FASTBOOT STAGE: waiting for ADB after the pending reboot.", flush=True)
                wait_for_transport(
                    [adb_bin, "-s", pending_serial, "get-state"], "device", adb_timeout, "ADB"
                )
            serial = select_adb_serial(adb_bin, fastboot_serial)
            _write_state(state_path, {**state, "device_serial": serial, "slots": slots})
            print(
                f"Resuming from {phase}; no further flash or reboot will be attempted ({serial}).",
                flush=True,
            )
            # State from an earlier process is not current readback evidence.
            for slot in selected:
                verify_adb_payload_readback(adb_bin, serial, slot, manifest["boot"]["sha256"], adb_timeout)
            _write_state(state_path, {**state, "phase": phase if staged else "READBACK_VERIFIED",
                                      "device_serial": serial, "slots": slots})
        try:
            if staged:
                # Never overwrite an already configured/running feature just to reopen a forward.
                for feature in manifest["features"]:
                    _run_command([adb_bin, "-s", serial, "shell", "test", "!", "-e",
                                  f"/data/libreecho/features/{feature['name']}/staging"], adb_timeout)
                verify_device_features(adb_bin, serial, manifest, adb_timeout)
            else:
                stage_device_features(adb_bin, serial, cache_root, manifest, adb_timeout)
        except InstallerError:
            print("PAYLOAD STAGE: failed; collecting read-only ADB diagnostics.", flush=True)
            collect_adb_diagnostics(adb_bin, serial, min(adb_timeout, 30), "feature staging failure")
            raise
        _write_state(state_path, {"phase": "FEATURES_STAGED", "release": release,
                                  "bundle_sha256": state["bundle_sha256"]})
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
        for path, record in ((payload, feature["payload"]), (feature_manifest, feature["manifest"])):
            if path.stat().st_size != record["size"] or _sha256(path) != record["sha256"]:
                raise InstallerError(f"feature {name} cached {path.name} changed before staging")
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
            f"MANIFEST_FILE={remote_manifest}\n"
            f"MANIFEST_SHA256={feature['manifest']['sha256']}\n"
            f"MANIFEST_SIZE={feature['manifest']['size']}\n",
            encoding="ascii",
        )
        config.chmod(0o600)
        _run_command([adb_bin, "-s", serial, "push", str(config), "/tmp/libreecho-feature-stage.conf"], timeout)
        result = _run_command([adb_bin, "-s", serial, "shell", "sh", remote_script], timeout, check=False)
        if result.returncode != 0 or f"FEATURE_STAGE_OK:{name}" not in result.stdout:
            detail = (result.stderr or result.stdout).strip()[-500:]
            raise InstallerError(f"feature staging failed for {name}: {detail}")
        verify_device_features(adb_bin, serial, {"features": [feature]}, timeout)
        print(f"Feature {name} staged and verified.", flush=True)
        config.unlink(missing_ok=True)


def verify_device_features(
    adb_bin: str, serial: str, manifest: dict[str, Any], timeout: float = 180,
) -> None:
    """Read back each installed payload and manifest; an upload acknowledgement is not proof."""
    for feature in manifest["features"]:
        for kind, filename in (("payload", "payload.squashfs"), ("manifest", "manifest.json")):
            path = f"/data/libreecho/features/{feature['name']}/{filename}"
            output = _run_command(
                [adb_bin, "-s", serial, "shell", "sha256sum", path], timeout,
            ).stdout.strip()
            digest = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+\*?" + re.escape(path), output)
            if digest is None or digest.group(1).lower() != feature[kind]["sha256"].lower():
                raise InstallerError(f"feature {feature['name']} installed {kind} hash mismatch")


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
MANIFEST_SHA256=
MANIFEST_SIZE=
while IFS='=' read -r key value; do
    case "$key" in
        FEATURE_ID) FEATURE_ID=$value ;;
        PAYLOAD_SHA256) PAYLOAD_SHA256=$value ;;
        PAYLOAD_SIZE) PAYLOAD_SIZE=$value ;;
        PAYLOAD_FILE) PAYLOAD_FILE=$value ;;
        MANIFEST_FILE) MANIFEST_FILE=$value ;;
        MANIFEST_SHA256) MANIFEST_SHA256=$value ;;
        MANIFEST_SIZE) MANIFEST_SIZE=$value ;;
    esac
done < "$CONFIG"
case "$FEATURE_ID" in ''|.|..|*[!a-z0-9._-]*) echo FEATURE_STAGE_ID_INVALID; exit 1 ;; esac
[ -f "$PAYLOAD_FILE" ] || { echo FEATURE_STAGE_PAYLOAD_MISSING; exit 1; }
[ -f "$MANIFEST_FILE" ] && [ ! -L "$MANIFEST_FILE" ] || { echo FEATURE_STAGE_MANIFEST_MISSING; exit 1; }
case "$MANIFEST_SHA256" in ''|*[!0-9a-f]*) echo FEATURE_STAGE_MANIFEST_HASH_INVALID; exit 1 ;; esac
[ "${#MANIFEST_SHA256}" -eq 64 ] || { echo FEATURE_STAGE_MANIFEST_HASH_INVALID; exit 1; }
case "$MANIFEST_SIZE" in ''|*[!0-9]*) echo FEATURE_STAGE_MANIFEST_SIZE_INVALID; exit 1 ;; esac
[ "$MANIFEST_SIZE" -gt 0 ] || { echo FEATURE_STAGE_MANIFEST_SIZE_INVALID; exit 1; }
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
actual=$($BB sha256sum "$MANIFEST_FILE" | $BB awk '{print $1}')
[ "$actual" = "$MANIFEST_SHA256" ] || { echo FEATURE_STAGE_MANIFEST_HASH_MISMATCH; exit 1; }
actual_size=$($BB stat -c %s "$MANIFEST_FILE" 2>/dev/null)
[ "$actual_size" = "$MANIFEST_SIZE" ] || { echo FEATURE_STAGE_MANIFEST_SIZE_MISMATCH; exit 1; }
DEST=/data/libreecho/features/$FEATURE_ID
$BB mkdir -p "$DEST/staging"
$BB cp "$PAYLOAD_FILE" "$DEST/staging/payload.squashfs.new"
staged=$($BB sha256sum "$DEST/staging/payload.squashfs.new" | $BB awk '{print $1}')
[ "$staged" = "$PAYLOAD_SHA256" ] || { echo FEATURE_STAGE_COPY_HASH_MISMATCH; exit 1; }
# Prepare and verify BOTH files while the staging marker prevents activation.
$BB cp "$MANIFEST_FILE" "$DEST/staging/manifest.json.new"
staged=$($BB sha256sum "$DEST/staging/manifest.json.new" | $BB awk '{print $1}')
[ "$staged" = "$MANIFEST_SHA256" ] || { echo FEATURE_STAGE_MANIFEST_COPY_HASH_MISMATCH; exit 1; }
$BB rm -f "$DEST/payload.squashfs.previous"
if [ -f "$DEST/payload.squashfs" ]; then
    $BB mv "$DEST/payload.squashfs" "$DEST/payload.squashfs.previous"
fi
$BB mv "$DEST/staging/payload.squashfs.new" "$DEST/payload.squashfs"
$BB mv "$DEST/staging/manifest.json.new" "$DEST/manifest.json"
$BB sync || { echo FEATURE_STAGE_COMMIT_SYNC_FAILED; exit 1; }
$BB rmdir "$DEST/staging" || { echo FEATURE_STAGE_STAGING_CLEANUP_FAILED; exit 1; }
$BB sync || { echo FEATURE_STAGE_MARKER_SYNC_FAILED; exit 1; }
$BB rm -f "$CONFIG" "$PAYLOAD_FILE" "$MANIFEST_FILE"
echo "FEATURE_STAGE_OK:$FEATURE_ID"
"""


def resume(
    cache_root: Path | str = Path.home() / ".cache/libreecho-installer",
    state_root: Path | str = Path.home() / ".local/state/libreecho-installer",
    install_id: str = "default",
) -> dict[str, str]:
    state = _read_state(_state_path(Path(state_root), install_id))
    bundle = Path(cache_root) / state["release"] / "downloads" / (target_asset_prefix(state["release"], state.get("board", "radar_puffin")) + "-initial-install.tar")
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
    parser.add_argument("--target", choices=tuple(TARGET_INDEX), help="explicit board identity for BROM or cross-flashed LK")
    parser.add_argument("--release-repository", default=RELEASE_REPOSITORY)
    parser.add_argument("--download-root", type=Path, default=Path.home() / ".cache/libreecho-installer/downloads")
    parser.add_argument("--amonet-zip", type=Path, help="pinned Amonet ZIP (required only for locked devices)")
    parser.add_argument("--fastboot-bin", default="fastboot")
    parser.add_argument("--adb-bin", default="adb")
    parser.add_argument("--fastboot-serial", default="auto")
    parser.add_argument("--slots", choices=("a", "b", "both"), default="both")
    parser.add_argument("--local-port", type=int, default=18080)
    parser.add_argument("--brick-timeout", type=float, default=120)
    parser.add_argument("--fastboot-timeout", type=float, default=120)
    parser.add_argument("--adb-timeout", type=float, default=180)
    parser.add_argument("--no-open-browser", action="store_true")
    parser.add_argument("--execute-hardware", action="store_true")
    parser.add_argument(
        "--install-host-deps", action="store_true",
        help=(
            "install missing e2fsprogs (mke2fs/dumpe2fs) before any "
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
                            args.release_dir, args.amonet_zip,
                            release_repository=args.release_repository, download_root=args.download_root,
                            cache_root=args.cache_root,
                            state_root=args.state_root, install_id=args.install_id,
                            release_tag=args.release_tag, fastboot_bin=args.fastboot_bin,
                            adb_bin=args.adb_bin, fastboot_serial=args.fastboot_serial,
                            slots=args.slots, local_port=args.local_port,
                            brick_timeout=args.brick_timeout, fastboot_timeout=args.fastboot_timeout,
                            adb_timeout=args.adb_timeout, open_browser=not args.no_open_browser,
                            execute_hardware=args.execute_hardware or args.emulator_root is not None,
                            target=args.target,
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
                            target=args.target,
                            install_host_deps=args.install_host_deps,
                        )
                    elif args.action == "resume":
                        result = resume(args.cache_root, args.state_root, args.install_id)
                    else:
                        result = status(args.state_root, args.install_id)
                except InstallerError as error:
                    archive = collect_failure_evidence(args, str(error))
                    if archive is not None:
                        print(f"Failure evidence archive: {archive}", file=sys.stderr, flush=True)
                    else:
                        print("Failure evidence archive could not be created; installer log may still be available.", file=sys.stderr, flush=True)
                    print(f"ERROR: {error}", file=sys.stderr, flush=True)
                    raise SystemExit(1) from error
                print(json.dumps(result, sort_keys=True))
    except OSError as error:
        raise SystemExit(f"ERROR: cannot open installer log {ACTIVE_LOG_PATH}: {error}") from error


if __name__ == "__main__":
    main()
