#!/usr/bin/env python3
"""Validate the narrow, explicit Product OTA v2 workflow input contract."""
from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

EXPECTED_RELEASE = "0.14.0"
EXPECTED_BASE_RELEASE = "radar-puffin-v0.13.13"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
BASE_URL_PREFIX = (
    "https://github.com/aslater3/LibreEcho/releases/download/"
    f"{EXPECTED_BASE_RELEASE}/"
)


class InputError(ValueError):
    """Raised when an OTA workflow input is unsafe or unsupported."""


def validate_inputs(
    ota_format: str,
    ota_release: str,
    base_catalog_url: str,
    base_catalog_sha256: str,
    event_name: str,
    ref: str,
) -> dict[str, str]:
    """Return normalized inputs, retaining v1 as the default bridge."""
    if ota_format == "v1":
        if any((ota_release, base_catalog_url, base_catalog_sha256)):
            raise InputError("v1 does not accept OTA v2 inputs")
        return {
            "ota_format": "v1", "ota_release": "",
            "ota_base_catalog_url": "", "ota_base_catalog_sha256": "",
        }
    if ota_format != "v2":
        raise InputError("ota_format must be v1 or v2")
    if event_name != "workflow_dispatch":
        raise InputError("OTA v2 is an explicit workflow_dispatch opt-in")
    if not base_catalog_url and not base_catalog_sha256:
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", ota_release):
            raise InputError("OTA v2 requires a numeric candidate release")
        if ref != f"refs/heads/release/{ota_release}":
            raise InputError("OTA v2 candidate must match its release branch")
        return {"ota_format": "v2", "ota_release": ota_release,
                "ota_base_catalog_url": "", "ota_base_catalog_sha256": ""}
    if ref != f"refs/heads/release/{EXPECTED_RELEASE}":
        raise InputError(f"OTA v2 is only authorized for release/{EXPECTED_RELEASE}")
    if ota_release != EXPECTED_RELEASE:
        raise InputError(f"OTA v2 release must match the actual {EXPECTED_RELEASE} candidate")
    if not HEX64.fullmatch(base_catalog_sha256):
        raise InputError("OTA v2 base catalog SHA-256 must be 64 lowercase hex characters")
    if not base_catalog_url.startswith(BASE_URL_PREFIX):
        raise InputError("OTA v2 base catalog must come from the pinned prior Product release")
    parsed = urlsplit(base_catalog_url)
    catalog_name = Path(parsed.path).name
    if (parsed.scheme != "https" or parsed.netloc != "github.com"
            or parsed.query or parsed.fragment
            or parsed.path != BASE_URL_PREFIX.removeprefix("https://github.com") + catalog_name
            or re.fullmatch(
                rf"libreecho-{re.escape(EXPECTED_BASE_RELEASE)}-feature-catalog\.json",
                catalog_name,
            ) is None):
        raise InputError("OTA v2 base catalog URL is not an immutable release asset")
    return {
        "ota_format": "v2", "ota_release": EXPECTED_RELEASE,
        "ota_base_catalog_url": base_catalog_url,
        "ota_base_catalog_sha256": base_catalog_sha256,
    }


def verify_catalog(path: Path, expected_sha256: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise InputError("base catalog is not a regular file")
    if not HEX64.fullmatch(expected_sha256):
        raise InputError("base catalog SHA-256 is malformed")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise InputError("base catalog hash mismatch")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", dest="ota_format", required=True)
    parser.add_argument("--release", dest="ota_release", default="")
    parser.add_argument("--base-catalog-url", default="")
    parser.add_argument("--base-catalog-sha256", default="")
    parser.add_argument("--event", required=True)
    parser.add_argument("--ref", required=True)
    args = parser.parse_args()
    try:
        values = validate_inputs(
            args.ota_format, args.ota_release, args.base_catalog_url,
            args.base_catalog_sha256, args.event, args.ref,
        )
    except InputError as exc:
        parser.error(str(exc))
    for key, value in values.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
