#!/usr/bin/env bash
set -euo pipefail

FEATURE_ID="${1:?usage: ./package_feature_payload.sh <feature-id> <source-root> <payload.squashfs> <manifest.json>}"
SOURCE="${2:?usage: ./package_feature_payload.sh <feature-id> <source-root> <payload.squashfs> <manifest.json>}"
OUTPUT="${3:?usage: ./package_feature_payload.sh <feature-id> <source-root> <payload.squashfs> <manifest.json>}"
MANIFEST="${4:?usage: ./package_feature_payload.sh <feature-id> <source-root> <payload.squashfs> <manifest.json>}"

[[ "$FEATURE_ID" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || {
  echo "ERROR: invalid feature id: $FEATURE_ID" >&2
  exit 1
}
[[ -d "$SOURCE" && ! -L "$SOURCE" ]] || {
  echo "ERROR: feature source root is missing or is a symlink: $SOURCE" >&2
  exit 1
}
[[ ! -e "$OUTPUT" && ! -e "$MANIFEST" ]] || {
  echo "ERROR: refusing to overwrite feature payload or manifest" >&2
  exit 1
}
command -v mksquashfs >/dev/null || { echo "ERROR: mksquashfs not found" >&2; exit 1; }
command -v sha256sum >/dev/null || { echo "ERROR: sha256sum not found" >&2; exit 1; }
command -v python3 >/dev/null || { echo "ERROR: python3 not found" >&2; exit 1; }

while IFS= read -r -d '' symlink; do
  echo "ERROR: feature payload contains a symlink: $symlink" >&2
  exit 1
done < <(find "$SOURCE" -type l -print0)

mkdir -p "$(dirname -- "$OUTPUT")" "$(dirname -- "$MANIFEST")"
mksquashfs "$SOURCE" "$OUTPUT" -noappend -comp lz4 -all-root \
  -no-xattrs -mkfs-time 0 -all-time 0 -no-progress >/tmp/libreecho-feature-mksquashfs.log

python3 - "$FEATURE_ID" "$SOURCE" "$OUTPUT" "$MANIFEST" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

feature_id, source_name, output_name, manifest_name = sys.argv[1:]
source = Path(source_name).resolve()
output = Path(output_name).resolve()
files = {}
for path in sorted(source.rglob("*")):
    if not path.is_file() or path.is_symlink():
        continue
    relative = path.relative_to(source).as_posix()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    files[relative] = {
        "sha256": digest,
        "size": path.stat().st_size,
        "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
    }
payload = output.read_bytes()
manifest = {
    "schema_version": 1,
    "feature_id": feature_id,
    "format": "squashfs-lz4",
    "payload": {
        "filename": output.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    },
    "files": files,
}
Path(manifest_name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
PY

echo "feature_id=$FEATURE_ID"
echo "payload=$OUTPUT"
stat -c 'payload_size=%s' "$OUTPUT"
sha256sum "$OUTPUT"
