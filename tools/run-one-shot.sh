#!/usr/bin/env bash
# Verify the exact Product installer before executing it.
set -euo pipefail

TAG="${1:-latest}"
shift || true
action=one-shot
if [[ "${1:-}" == --continue ]]; then
  [[ "$TAG" != latest ]] || { echo "ERROR: continuation requires the saved immutable release tag, not latest" >&2; exit 2; }
  action=continue-one-shot
  shift
fi
repo="${LIBREECHO_RELEASE_REPOSITORY:-aslater3/LibreEcho}"
target="${LIBREECHO_TARGET:-}"
fastboot_bin=fastboot
serial=auto
options=("$@")
for ((i=0; i<${#options[@]}; i++)); do
  case "${options[i]}" in
    --target) target="${options[i+1]:?missing target}" ;;
    --fastboot-bin) fastboot_bin="${options[i+1]:?missing fastboot binary}" ;;
    --fastboot-serial) serial="${options[i+1]:?missing fastboot serial}" ;;
  esac
done
if [[ -z "$target" ]]; then
  if [[ "$serial" == auto ]]; then
    mapfile -t devices < <("$fastboot_bin" devices | cut -f1)
    [[ "${#devices[@]}" == 1 ]] || { echo 'ERROR: specify --target for BROM or ambiguous stock fastboot identity' >&2; exit 2; }
    serial="${devices[0]}"
  fi
  product="$("$fastboot_bin" -s "$serial" getvar product 2>&1)"
  # Generated Product fastboot mapping; target tests compare the descriptors.
  case "$product" in
    *'product: RADAR'*) target=radar_puffin ;;
    *'product: BISCUIT'*) target=biscuit ;;
    *) echo 'ERROR: unknown product; explicit --target required' >&2; exit 2 ;;
  esac
fi
case "$target" in
  radar_puffin) slug=radar-puffin ;;
  biscuit) slug=biscuit ;;
  *) echo "ERROR: unknown target: $target" >&2; exit 2 ;;
esac

if [[ "$TAG" == latest ]]; then
  release_json="$(curl --fail --location --silent --show-error \
    "https://api.github.com/repos/${repo}/releases/latest")"
  TAG="$(printf '%s' "$release_json" | python3 -c '
import json, sys
release = json.load(sys.stdin)
if release.get("draft") or release.get("prerelease"):
    raise SystemExit("latest release is not a published stable release")
print(release.get("tag_name", ""))
')"
  # A repository-global latest is usable ONLY if it contains this target's
  # installation asset. It must never silently select another board.
  expected_prefix="libreecho-$TAG"
  [[ "$target" == radar_puffin ]] || expected_prefix="libreecho-$slug-${TAG#radar-puffin-}"
  printf '%s' "$release_json" | python3 -c '
import json,sys
r=json.load(sys.stdin)
if sys.argv[1]+"-initial-install.tar" not in {a["name"] for a in r.get("assets", [])}:
    raise SystemExit("latest stable has no selected-target install; specify an immutable combined release tag")
' "$expected_prefix"
  echo "Resolved target-qualified latest stable release: ${TAG} target=${target}"
fi

if [[ ! "$TAG" =~ ^radar-puffin-(v[0-9]+\.[0-9]+\.[0-9]+|(nightly|build)-[0-9a-f-]+)$ ]]; then
  echo "ERROR: invalid release tag: ${TAG:-<missing>}" >&2
  echo "Usage: $0 [latest|RADAR_PUFFIN_RELEASE_TAG] [installer options...]" >&2
  exit 2
fi

base="https://github.com/${repo}/releases/download/${TAG}"
prefix="libreecho-${TAG}"
[[ "$target" == radar_puffin ]] || prefix="libreecho-${slug}-${TAG#radar-puffin-}"
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
if python3 "$work/${prefix}-installer.py" "$action" --release-tag "$TAG" --target "$target" "$@"; then
  status=0
else
  status=$?
fi
exit "$status"
