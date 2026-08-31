#!/usr/bin/env python3
"""Derive LibreEcho's reviewed TTS voice models from pinned upstream Piper voices.

LibreEcho ships two VITS voice models that are byte-for-byte reproducible
derivatives of the pinned upstream Piper voices (rhasspy/piper-voices,
revision ea046e8458f6acd997706d6e6066a022b42f6fb1): each is the upstream
model blob with ONNX ``metadata_props`` appended. The packaged artifacts are
identified in ``tools/mt8163-arm32/tts/package_feature.sh`` (LibreEcho-Kernel)
and in its third-party notices:

  northern-male    d23e7891af7062eb188283dba94866e25ffd5b01a0d9fb9a23c71a39b75b2308
  southern-female  cf7f487689da2ec115cb5e9b5fb5ff4450f24e0c45565e0b72dd1eb4ed4caf65

Graph and tensor data are untouched; only metadata properties are appended.
The append encodes each ``StringStringEntryProto`` (field 1: key, field 2:
value) wrapped as ``ModelProto.metadata_props`` entries (field 14) and
concatenates them to the upstream bytes. Protobuf fields are
order-independent, so the result parses as a valid ``ModelProto`` and is
byte-identical to the reviewed derivatives. No ONNX library is required.

The voice list, exact property sets, and reviewed hashes live in
``build/inputs/tts-voice-metadata.json``. This script fails closed when an
upstream file is missing or a derived hash does not equal the reviewed hash.

Usage:
  derive-tts-model.py --deps-root <public-deps-dir> [--spec <path>]
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path


def encode_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_string_field(field_no: int, s: str) -> bytes:
    data = s.encode()
    tag = encode_varint((field_no << 3) | 2)
    return tag + encode_varint(len(data)) + data


def encode_metadata_entry(key: str, value: str) -> bytes:
    inner = encode_string_field(1, key) + encode_string_field(2, value)
    tag = encode_varint((14 << 3) | 2)  # ModelProto.metadata_props
    return tag + encode_varint(len(inner)) + inner


def derive(upstream_path: Path, output_path: Path, props: list) -> str:
    data = upstream_path.read_bytes()
    blob = b"".join(encode_metadata_entry(k, v) for k, v in props)
    output_path.write_bytes(data + blob)
    return hashlib.sha256(output_path.read_bytes()).hexdigest()


def default_spec_path() -> Path:
    return Path(__file__).resolve().parent.parent / "inputs" / "tts-voice-metadata.json"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Derive reviewed LibreEcho TTS voice models from upstream Piper voices."
    )
    parser.add_argument("--deps-root", required=True, help="public-deps directory")
    parser.add_argument("--spec", default=str(default_spec_path()))
    args = parser.parse_args()

    spec = json.loads(Path(args.spec).read_text())
    deps = Path(args.deps_root)
    if not deps.is_dir():
        print(f"ERROR: deps root is not a directory: {deps}", file=sys.stderr)
        return 1

    voices = spec.get("voices")
    if not isinstance(voices, dict) or not voices:
        print("ERROR: spec has no voices", file=sys.stderr)
        return 1

    for name, voice in voices.items():
        upstream = deps / voice["upstream_file"]
        output = deps / voice["output_file"]
        if not upstream.is_file():
            print(f"ERROR: upstream voice model missing: {upstream}", file=sys.stderr)
            return 1
        props = [(k, v) for k, v in voice["metadata_props"]]
        sha = derive(upstream, output, props)
        if sha != voice["reviewed_sha256"]:
            print(
                f"ERROR: derived {name} hash {sha} != reviewed {voice['reviewed_sha256']}",
                file=sys.stderr,
            )
            return 1
        print(f"{name}: derived {output.name} sha256={sha} (matches reviewed contract)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
