#!/usr/bin/env bash
# Resolve a release alias, verify the installer, then run it.
set -euo pipefail

TAG="${1:-}"
shift || true
repo="${LIBREECHO_RELEASE_REPOSITORY:-aslater3/LibreEcho}"
work="$(mktemp -d "${TMPDIR:-/tmp}/libreecho-installer.XXXXXXXX")"
trap 'rm -rf "$work"' EXIT

if [[ "$TAG" == latest ]]; then
  curl --fail --location --silent --show-error \
    -o "$work/release.json" \
    "https://api.github.com/repos/${repo}/releases/latest"
  TAG="$(python3 - "$work/release.json" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    release = json.load(stream)
tag = release.get("tag_name")
if (release.get("draft") or release.get("prerelease") or
        not isinstance(tag, str) or
        not re.fullmatch(r"radar-puffin-v[0-9]+\.[0-9]+\.[0-9]+", tag)):
    raise SystemExit("ERROR: latest release is not a published stable LibreEcho tag")
print(tag)
PY
  )"
fi

if [[ ! "$TAG" =~ ^radar-puffin-(v[0-9]+\.[0-9]+\.[0-9]+|(nightly|build)-[0-9a-f-]+)$ ]]; then
  echo "ERROR: invalid release tag: ${TAG:-<missing>}" >&2
  echo "Usage: $0 latest|RADAR_PUFFIN_RELEASE_TAG [installer options...]" >&2
  exit 2
fi

base="https://github.com/${repo}/releases/download/${TAG}"
prefix="libreecho-${TAG}"
checksums="$work/${prefix}-SHA256SUMS"
installer="$work/${prefix}-installer.py"
curl --fail --location --silent --show-error \
  -o "$checksums" "$base/${prefix}-SHA256SUMS"
curl --fail --location --silent --show-error \
  -o "$installer" "$base/${prefix}-installer.py"
expected="$(awk -v name="${prefix}-installer.py" '$2 == name { print $1; found=1 } END { if (!found) exit 1 }' "$checksums")"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
  echo "ERROR: installer hash is missing or malformed in release inventory" >&2
  exit 1
}
printf '%s  %s\n' "$expected" "$installer" | sha256sum -c -
echo "Installer checksum verified for ${TAG}."
if python3 "$installer" one-shot --release-tag "$TAG" "$@"; then
  status=0
else
  status=$?
fi
exit "$status"
