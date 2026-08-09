#!/usr/bin/env python3
"""Fail unless every redistributed public-release component is cleared."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

sys.dont_write_bytecode = True


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--components", type=Path,
        default=root / "release/components.json",
        help="component catalog to enforce",
    )
    args = parser.parse_args()
    prepare_path = root / "tools/prepare-release.py"
    spec = importlib.util.spec_from_file_location("prepare_release", prepare_path)
    if spec is None or spec.loader is None:
        raise SystemExit("ERROR: release component validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    components = module.load_components(args.components)
    print(f"component_gate=cleared component_count={len(components)}")


if __name__ == "__main__":
    main()
