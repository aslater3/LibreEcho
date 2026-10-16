#!/usr/bin/env python3
"""Validate a stable Product release before any GitHub mutation."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ota_v2_product import ContractError, validate_stable_publisher  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--release-tag", required=True)
    args = parser.parse_args()
    try:
        assets = validate_stable_publisher(args.release_dir, args.release_tag)
    except (ContractError, OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"stable_release_gate=PASS tag={args.release_tag} assets={len(assets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
