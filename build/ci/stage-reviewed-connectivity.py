#!/usr/bin/env python3
"""Stage the reviewed MT8163 connectivity helper binaries, fail closed.

The connectivity helpers carry a byte-exact recovery-image contract:
``build_recovery_image.py`` pins each binary's size and SHA-256 in
``CONNECTIVITY_HELPERS`` and the independent ``verify_recovery_image.py``
enforces the same table. The reviewed bytes were produced by the dedicated
lane's Alpine 15.2.0 packaged ``armv7-alpine-linux-musleabihf-gcc``
(pmbootstrap chroot); a hosted pipeline builds its toolchain from source,
which cannot reproduce those bytes, so the public pipeline vendors the
reviewed binaries in ``build/inputs/reviewed/connectivity/`` instead of
rebuilding them.

This script:

1. imports the contract table directly from ``build_recovery_image.py``
   (single source of truth, never duplicated);
2. verifies every vendored binary's size and SHA-256 against it;
3. verifies the vendored ``connectivity-source.json`` still matches the
   current platform connectivity sources and the recorded compiler, so a
   source change or corrupted metadata fails closed instead of shipping
   stale bytes;
4. installs the verified files into the output directory.
"""

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import NoReturn

COMPILER_CONTRACT = "armv7-alpine-linux-musleabihf-gcc (Alpine 15.2.0) 15.2.0"
SOURCE_FILES = [
    "wmt_configure.c", "wmt_responder.c", "wmt_bt_on.c",
    "wmt_stock_compat.c", "wmt_launcher.c", "wmt_ioctl.h", "SOURCE.lock",
]


def fail(message: str) -> NoReturn:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_contract(tools_dir: Path) -> dict:
    module_path = tools_dir / "build_recovery_image.py"
    if not module_path.is_file():
        fail(f"recovery image builder is missing: {module_path}")
    sys.path.insert(0, str(tools_dir))
    try:
        spec = importlib.util.spec_from_file_location("build_recovery_image", module_path)
        if spec is None or spec.loader is None:
            fail(f"cannot load recovery image builder: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    contract = getattr(module, "CONNECTIVITY_HELPERS", None)
    if not isinstance(contract, dict) or len(contract) != 5:
        fail("CONNECTIVITY_HELPERS contract table is unavailable")
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage reviewed connectivity helpers, fail closed on the contract."
    )
    parser.add_argument("--vendored", required=True)
    parser.add_argument("--tools-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    vendored = Path(args.vendored)
    tools_dir = Path(args.tools_dir)
    output = Path(args.output)
    if not vendored.is_dir():
        fail(f"vendored connectivity directory is missing: {vendored}")
    if not tools_dir.is_dir():
        fail(f"platform tooling directory is missing: {tools_dir}")

    contract = load_contract(tools_dir)

    # Verify the vendored metadata against the live platform sources and the
    # recorded compiler so stale or corrupted vendored bytes fail closed.
    metadata_path = vendored / "connectivity-source.json"
    if not metadata_path.is_file():
        fail(f"vendored connectivity metadata is missing: {metadata_path}")
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("compiler") != COMPILER_CONTRACT:
        fail(
            "vendored connectivity metadata does not record the reviewed "
            f"compiler: {metadata.get('compiler')!r}"
        )
    recorded_sources = metadata.get("sources")
    if not isinstance(recorded_sources, dict):
        fail("vendored connectivity metadata has no source records")
    for name in SOURCE_FILES:
        source = tools_dir / "connectivity" / name
        if not source.is_file():
            fail(f"platform connectivity source is missing: {source}")
        digest = sha256(source)
        if recorded_sources.get(name) != digest:
            fail(
                f"platform connectivity source changed; vendored reviewed "
                f"binaries are stale for {name}: recorded="
                f"{recorded_sources.get(name)} current={digest}"
            )
    recorded_outputs = metadata.get("outputs")
    if not isinstance(recorded_outputs, dict) or len(recorded_outputs) != 5:
        fail("vendored connectivity metadata has incomplete output records")

    output.mkdir(parents=True, exist_ok=True)
    staged = 0
    for runtime_path, (asset_name, expected_size, expected_sha) in contract.items():
        file_name = runtime_path.rsplit("/", 1)[-1]
        source = vendored / file_name
        if not source.is_file():
            fail(f"vendored reviewed helper is missing: {source}")
        data = source.read_bytes()
        if len(data) != expected_size:
            fail(
                f"vendored helper {asset_name} size mismatch: "
                f"expected={expected_size} actual={len(data)}"
            )
        if hashlib.sha256(data).hexdigest() != expected_sha:
            fail(f"vendored helper {asset_name} hash is not the reviewed contract")
        record = recorded_outputs.get(file_name)
        if not isinstance(record, dict) or record.get("sha256") != expected_sha \
                or record.get("size") != expected_size:
            fail(f"vendored metadata disagrees with the contract for {file_name}")
        destination = output / file_name
        destination.write_bytes(data)
        destination.chmod(0o755)
        staged += 1
    if staged != 5:
        fail(f"staged {staged} helpers, expected 5")

    import shutil
    shutil.copy2(metadata_path, output / "connectivity-source.json")
    print(f"connectivity helpers staged from reviewed vendored bytes ({staged} binaries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
