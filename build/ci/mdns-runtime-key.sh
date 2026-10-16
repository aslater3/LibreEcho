#!/usr/bin/env bash
# Print the checkout-path-independent identity of the shared mDNS discovery
# runtime that the recovery image actually consumes.
#
# The identity binds the exact package lock, the verified runtime inventory
# (manifest and packages), the runtime tree, and the Platform extraction code
# when it is available, so any change to the locked packages, the extraction
# code, or the packaged runtime invalidates the component identity.
set -euo pipefail

REPO="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
CACHE_TOOL="$REPO/component-cache.py"
runtime="${1:?usage: mdns-runtime-key.sh <mdns-runtime-root> [platform-mdns-dir]}"
platform_mdns="${2:-}"

[[ -f "$CACHE_TOOL" && ! -L "$CACHE_TOOL" ]] || {
  echo "ERROR: component cache tool is missing: $CACHE_TOOL" >&2
  exit 1
}
[[ -d "$runtime" && ! -L "$runtime" ]] || {
  echo "ERROR: shared mDNS runtime is missing or unsafe: $runtime" >&2
  exit 1
}
[[ -f "$runtime/manifest.json" && ! -L "$runtime/manifest.json" ]] || {
  echo "ERROR: shared mDNS runtime manifest is missing: $runtime/manifest.json" >&2
  exit 1
}
[[ -f "$runtime/packages.json" && ! -L "$runtime/packages.json" ]] || {
  echo "ERROR: shared mDNS runtime package inventory is missing: $runtime/packages.json" >&2
  exit 1
}
for input in "$REPO/inputs/mdns-packages.lock.json" "$REPO/inputs/mdns-source-offers.json"; do
  [[ -f "$input" && ! -L "$input" ]] || {
    echo "ERROR: shared mDNS release input is missing: $input" >&2
    exit 1
  }
done

key_args=(
  --value "runtime_layout=libreecho-mdns-runtime/v1"
  --file "runtime-manifest=$runtime/manifest.json"
  --file "runtime-packages=$runtime/packages.json"
  --tree "runtime-root=$runtime/root"
  --file "package-lock=$REPO/inputs/mdns-packages.lock.json"
  --file "source-offers=$REPO/inputs/mdns-source-offers.json"
)

if [[ -n "$platform_mdns" ]]; then
  [[ -d "$platform_mdns" && ! -L "$platform_mdns" ]] || {
    echo "ERROR: Platform shared mDNS directory is missing or unsafe: $platform_mdns" >&2
    exit 1
  }
  index=0
  while IFS= read -r -d '' source; do
    key_args+=(--file "platform-mdns-$index=$source")
    index=$((index + 1))
  done < <(find "$platform_mdns" -maxdepth 1 -type f \
    \( -name '*.py' -o -name '*.conf' \) -print0 | LC_ALL=C sort -z)
  ((index > 0)) || {
    echo "ERROR: Platform shared mDNS directory contains no extraction inputs" >&2
    exit 1
  }
fi

exec python3 -B "$CACHE_TOOL" key --component mdns-runtime "${key_args[@]}"
