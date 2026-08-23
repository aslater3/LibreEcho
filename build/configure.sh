#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd -- "$(dirname -- "$0")" && pwd -P)"
BUILD_ROOT="${LIBREECHO_BUILD_ROOT:?set LIBREECHO_BUILD_ROOT to an external job-local directory}"
PRIVATE_ROOT="${LIBREECHO_PRIVATE_ROOT:?set LIBREECHO_PRIVATE_ROOT to an external private directory}"

case "$(realpath -m -- "$BUILD_ROOT")" in
  "$REPO"|"$REPO"/*) echo "ERROR: build root must be outside the repository" >&2; exit 1 ;;
esac
case "$(realpath -m -- "$PRIVATE_ROOT")" in
  "$REPO"|"$REPO"/*) echo "ERROR: private root must be outside the repository" >&2; exit 1 ;;
esac

mkdir -p "$BUILD_ROOT/work" "$BUILD_ROOT/generated" "$BUILD_ROOT/out" "$PRIVATE_ROOT"
chmod 0700 "$PRIVATE_ROOT"
printf '%s\n' \
  "build_repository=$REPO" \
  "build_root=$BUILD_ROOT" \
  "private_root=$PRIVATE_ROOT" \
  'status=CONFIGURED'
