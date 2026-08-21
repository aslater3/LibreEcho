#!/usr/bin/env bash
# Public hosted build entrypoint. It refuses to use private/local fallbacks.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
OUTPUT=image-and-features
while (($#)); do
  case "$1" in
    --release-output) OUTPUT="${2:?missing output mode}"; shift 2 ;;
    --no-publish) shift ;;
    *) printf 'ERROR: unsupported option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
[[ "$OUTPUT" == image-and-features ]] || { echo 'ERROR: only image-and-features is public'; exit 2; }

python3 "$ROOT/ci/fetch-public-deps.py" "$ROOT/inputs/public-inputs.json"
printf '%s\n' 'ERROR: public dependency inventory is blocked; no private fallback is permitted' >&2
exit 3
