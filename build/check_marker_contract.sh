#!/usr/bin/env bash
# Build-time guard: the kernel and recovery service must both be able to write
# FASTBOOT_PLEASE before an image can be published for flashing.
set -euo pipefail
SYSMAP="${1:-}"
PROFILE="${2:-development}"
KERNEL_SRC="${3:-}"
TOOLING_SRC="${4:-}"
[[ -f "$SYSMAP" ]] || { echo "ERROR: System.map required" >&2; exit 1; }
[[ -d "$KERNEL_SRC" ]] || { echo "ERROR: explicit kernel source required" >&2; exit 1; }
[[ -d "$TOOLING_SRC" ]] || { echo "ERROR: explicit tooling source required" >&2; exit 1; }

case "$PROFILE" in
  development)
    for symbol in marker_thread echo_fastboot_marker_init __initcall_echo_fastboot_marker_init2; do
      grep -Eq "[[:space:]]$symbol$" "$SYSMAP" || {
        echo "ERROR: kernel marker symbol missing: $symbol" >&2; exit 1;
      }
    done
    ;;
  ota)
    for symbol in marker_thread echo_fastboot_marker_init __initcall_echo_fastboot_marker_init2; do
      if grep -Eq "[[:space:]]$symbol$" "$SYSMAP"; then
        echo "ERROR: release OTA image contains development marker symbol: $symbol" >&2
        exit 1
      fi
    done
    ;;
  *) echo "ERROR: invalid image profile: $PROFILE" >&2; exit 1 ;;
esac

MARKER_SRC="$KERNEL_SRC/drivers/misc/mediatek/echo_fastboot_marker.c"
INIT_SRC="$TOOLING_SRC/tools/mt8163-arm32/initramfs/libreecho-init"
if [[ "$PROFILE" == development ]]; then
  [[ -f "$MARKER_SRC" ]] || { echo "ERROR: development marker source missing" >&2; exit 1; }
  grep -q 'FASTBOOT_PLEASE' "$MARKER_SRC" || { echo "ERROR: kernel marker literal missing" >&2; exit 1; }
  grep -q 'written != (ssize_t)len' "$MARKER_SRC" || { echo "ERROR: kernel marker short-write check missing" >&2; exit 1; }
  grep -q 'WRITE_RETRIES' "$MARKER_SRC" || { echo "ERROR: kernel marker retry guard missing" >&2; exit 1; }
  grep -q 'vfs_read' "$MARKER_SRC" || { echo "ERROR: kernel marker readback check missing" >&2; exit 1; }
fi
grep -q 'fastboot-please-written' "$INIT_SRC" || { echo "ERROR: userspace marker readback path missing" >&2; exit 1; }
grep -q 'marker_attempt' "$INIT_SRC" || { echo "ERROR: userspace marker retry guard missing" >&2; exit 1; }
grep -q 'PARTNAME=expdb' "$INIT_SRC" || { echo "ERROR: expdb identity gate missing" >&2; exit 1; }
grep -q 'image-profile' "$INIT_SRC" || { echo "ERROR: image profile gate missing" >&2; exit 1; }

echo "marker_contract=PASS profile=$PROFILE"
