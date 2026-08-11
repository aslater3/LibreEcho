#!/usr/bin/env python3
"""Reject workstation, device, and private-network identities in release metadata."""

from __future__ import annotations

import argparse
import ipaddress
import re
from pathlib import Path

IPV4 = re.compile(r"(?<![0-9.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])")
MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
RUN_ID = re.compile(r"(?<![0-9A-Za-z])20[0-9]{6}T[0-9]{6}Z-[0-9A-Za-z][0-9A-Za-z-]*(?![0-9A-Za-z])")
PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def violations(root: Path) -> list[str]:
    failures: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        for match in RUN_ID.finditer(str(relative)):
            failures.append(f"{relative}: private run ID {match.group(0)}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in ("/home/", "/dev/tty"):
            if marker in text:
                failures.append(f"{relative}: private marker {marker!r}")
        for match in RUN_ID.finditer(text):
            failures.append(f"{relative}: private run ID {match.group(0)}")
        for match in MAC.finditer(text):
            failures.append(f"{relative}: MAC address {match.group(0)}")
        for match in IPV4.finditer(text):
            try:
                address = ipaddress.ip_address(match.group(0))
            except ValueError:
                continue
            if any(address in network for network in PRIVATE_NETWORKS):
                failures.append(f"{relative}: private IPv4 address {address}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path("release"))
    args = parser.parse_args()
    failures = violations(args.root)
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print(f"public_metadata_gate=cleared root={args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
