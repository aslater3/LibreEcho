#!/usr/bin/env bash
# Verify and run the exact Product installer.
set -euo pipefail

TAG="${1:-}"
shift || true
repo="${LIBREECHO_RELEASE_REPOSITORY:-aslater3/LibreEcho}"
work="$(mktemp -d "${TMPDIR:-/tmp}/libreecho-installer.XXXXXXXX")"
trap 'rm -rf "$work"' EXIT

if [[ "$TAG" == latest ]]; then
  source_tag=latest
  curl --fail --location --silent --show-error \
    -o "$work/release.json" \
    "https://api.github.com/repos/${repo}/releases/tags/latest"
  python3 - "$work/release.json" >"$work/latest-meta" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    release = json.load(stream)
if release.get("draft") or release.get("prerelease"):
    raise SystemExit("ERROR: latest release is not a published stable release")
assets = release.get("assets")
if not isinstance(assets, list):
    raise SystemExit("ERROR: latest release asset list is missing")
names = [asset.get("name") for asset in assets if isinstance(asset, dict)]
checksums = [
    name for name in names
    if isinstance(name, str)
    and re.fullmatch(r"libreecho-(radar-puffin-v[0-9]+\\.[0-9]+\\.[0-9]+)-SHA256SUMS", name)
]
if len(checksums) != 1:
    raise SystemExit("ERROR: latest release does not have exactly one stable checksum inventory")
prefix = checksums[0][:-len("-SHA256SUMS")]
resolved_tag = prefix[len("libreecho-"):]
required = {f"{prefix}-installer.py", f"{prefix}-initial-install.tar", checksums[0]}
selected = []
for name in names:
    if not isinstance(name, str) or name != name.split("/")[-1] or ".." in name:
        raise SystemExit("ERROR: latest release contains an unsafe asset name")
    if name == checksums[0] or name.startswith(prefix + "-") or name == prefix + ".ota.tar":
        selected.append(name)
if not required.issubset(selected):
    raise SystemExit("ERROR: latest release is missing required installer assets")
print(resolved_tag)
print(prefix)
print("\n".join(sorted(set(selected))))
PY
  {
    read -r TAG
    read -r prefix
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      curl --fail --location --silent --show-error \
        -o "$work/$name" \
        "https://github.com/${repo}/releases/download/${source_tag}/${name}"
    done
  } <"$work/latest-meta"
  checksums="$work/${prefix}-SHA256SUMS"
  installer="$work/${prefix}-installer.py"
  (cd "$work" && sha256sum -c "$(basename "$checksums")")
  echo "Latest stable release resolved to ${TAG}; release assets verified."
  exec python3 "$installer" one-shot --release-dir "$work" --release-tag "$TAG" "$@"
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
echo "Installer checksum verified."
exec python3 "$installer" one-shot --release-tag "$TAG" "$@"
