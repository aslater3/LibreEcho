#!/usr/bin/env bash
# Execute the preserved mature builder with an explicit hosted dependency root.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
OUTPUT=image-and-features
while (($#)); do
  case "$1" in
    --release-output) OUTPUT="${2:?missing output mode}"; shift 2 ;;
    --no-publish) shift ;;
    *) echo "ERROR: unsupported option: $1" >&2; exit 2 ;;
  esac
done
[[ "$OUTPUT" == image-and-features ]] || { echo "ERROR: only image-and-features is public" >&2; exit 2; }
: "${LIBREECHO_PUBLIC_DEPS_ROOT:?set LIBREECHO_PUBLIC_DEPS_ROOT to the verified hosted dependency directory}"
: "${LIBREECHO_KERNEL_SRC:?set exact Linux source checkout}"
: "${LIBREECHO_TOOLING_SRC:?set exact Platform source checkout}"
: "${LIBREECHO_UI_SRC:?set exact UI source checkout}"
: "${LIBREECHO_PRODUCT_SRC:?set exact Product source checkout}"
: "${LIBREECHO_MUSL_CROSS_PREFIX:?set generated public ARM32 musl compiler prefix}"
: "${LIBREECHO_OTA_MUSL_NATIVE_ROOT:?set generated public native toolchain root}"
: "${LIBREECHO_OTA_MUSL_SYSROOT:?set generated public ARM32 sysroot}"
export LIBREECHO_INPUTS_ROOT="$LIBREECHO_PUBLIC_DEPS_ROOT"
export LIBREECHO_BUILD_ROOT="${LIBREECHO_BUILD_ROOT:-${RUNNER_TEMP:?set RUNNER_TEMP}/libreecho-build}"
export LIBREECHO_PRIVATE_ROOT="${LIBREECHO_PRIVATE_ROOT:-$RUNNER_TEMP/libreecho-private}"
export LIBREECHO_PUBLIC_RELEASE=1 LIBREECHO_FEATURE_POLICY=community-noncommercial
export LIBREECHO_OTA_SIGNING_MODE=github LIBREECHO_UPDATE_CHANNEL=dev JOBS=2
exec "$ROOT/build.sh" --defconfig --profile ota --service-profile production --feature-policy community-noncommercial --no-publish
