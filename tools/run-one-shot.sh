#!/usr/bin/env bash
# Verify the exact Product installer before executing it.
set -euo pipefail

TAG="${1:-}"
shift || true
if [[ ! "$TAG" =~ ^radar-puffin-(v[0-9]+\.[0-9]+\.[0-9]+|(nightly|build)-[0-9a-f-]+)$ ]]; then
  echo "ERROR: invalid release tag: ${TAG:-<missing>}" >&2
  echo "Usage: $0 RADAR_PUFFIN_RELEASE_TAG [installer options...]" >&2
  exit 2
fi

repo="${LIBREECHO_RELEASE_REPOSITORY:-aslater3/LibreEcho}"
base="https://github.com/${repo}/releases/download/${TAG}"
prefix="libreecho-${TAG}"
work="$(mktemp -d "${TMPDIR:-/tmp}/libreecho-installer.XXXXXXXX")"
trap 'rm -rf "$work"' EXIT

curl --fail --location --silent --show-error \
  -o "$work/SHA256SUMS" "$base/${prefix}-SHA256SUMS"
curl --fail --location --silent --show-error \
  -o "$work/${prefix}-installer.py" "$base/${prefix}-installer.py"

expected="$(awk -v name="${prefix}-installer.py" '$2 == name { print $1; found=1 } END { if (!found) exit 1 }' "$work/SHA256SUMS")"
[[ "$expected" =~ ^[0-9a-f]{64}$ ]] || {
  echo "ERROR: installer hash is missing or malformed in release inventory" >&2
  exit 1
}
printf '%s  %s\n' "$expected" "$work/${prefix}-installer.py" | sha256sum -c -
echo "Installer checksum verified: ${expected}"
exec python3 "$work/${prefix}-installer.py" one-shot --release-tag "$TAG" "$@"
