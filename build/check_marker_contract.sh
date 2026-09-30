#!/usr/bin/env bash
# Build-time guard for the amonet v2.0.0 boot-chain contract (Platform #195).
#
# On amonet v2.0.0, expdb (Linux partition 7) holds the LK-stage kaeru payload
# at offset 0.  Writing FASTBOOT_PLEASE there, or erasing it, destroys the boot
# chain one boot later.  No kernel or initramfs path may target expdb.  The
# development kernel keeps only the misc BCB retry reset; OTA images carry no
# development marker driver at all.
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
        echo "ERROR: kernel BCB reset symbol missing: $symbol" >&2; exit 1;
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
UPDATE_SRC="$TOOLING_SRC/tools/mt8163-arm32/initramfs/libreecho-update"
for source in "$INIT_SRC" "$UPDATE_SRC"; do
  [[ -f "$source" ]] || { echo "ERROR: initramfs source missing: $source" >&2; exit 1; }
done

# expdb must never be a write target, in any profile.
forbid() {
  local file=$1 pattern=$2 what=$3
  if grep -Eq -- "$pattern" "$file"; then
    echo "ERROR: $what in $file (amonet v2.0.0 stores kaeru in expdb; see Platform #195)" >&2
    exit 1
  fi
}
for source in "$INIT_SRC" "$UPDATE_SRC"; do
  forbid "$source" 'FASTBOOT_PLEASE' 'FASTBOOT_PLEASE marker literal'
  forbid "$source" 'mmcblk0p7|EXPDB' 'expdb device reference'
done

if [[ "$PROFILE" == development ]]; then
  [[ -f "$MARKER_SRC" ]] || { echo "ERROR: development BCB reset source missing" >&2; exit 1; }
  forbid "$MARKER_SRC" 'FASTBOOT_PLEASE' 'FASTBOOT_PLEASE marker literal'
  forbid "$MARKER_SRC" 'mmcblk0p7|EXPDB' 'expdb device reference'
  grep -q '"/dev/mmcblk0p8"' "$MARKER_SRC" || { echo "ERROR: BCB reset no longer targets misc" >&2; exit 1; }
  grep -q 'written != (ssize_t)len' "$MARKER_SRC" || { echo "ERROR: BCB reset short-write check missing" >&2; exit 1; }
  grep -q 'WRITE_RETRIES' "$MARKER_SRC" || { echo "ERROR: BCB reset retry guard missing" >&2; exit 1; }
  grep -q 'vfs_read' "$MARKER_SRC" || { echo "ERROR: BCB reset readback check missing" >&2; exit 1; }
fi
grep -q 'image-profile' "$INIT_SRC" || { echo "ERROR: image profile gate missing" >&2; exit 1; }

echo "marker_contract=PASS profile=$PROFILE expdb=untouched"
