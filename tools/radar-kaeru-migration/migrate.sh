#!/bin/bash
# radar-kaeru-migration: convert a radar_puffin running the OLD biscuit/k32 amonet
# chain to the current kaeru-based radar amonet chain (radar v1.0.0 package).
#
# Plan 1 from radar-rechain-plan.md: chain swap only, KEEP the legacy `_x` layout.
# Not written: GPT, boot_a_x/boot_b_x (OS image stays), misc, recovery, userdata.
#
# Proven write/readback pattern copied from ../radar-full-stock-20261009/flash.sh.
# Chain-version gate before touching expdb per standing rule (2026-09-24):
# never erase/write expdb without first identifying the chain on the device.
#
# Usage: ./migrate.sh -s SERIAL [--dry-run] [--verify]
#   --dry-run : gates + backups only, no writes
#   --verify  : after writes, reboot to fastboot and check getvar product/unlock
set -u
cd "$(dirname "$0")"

[ "${1:-}" = "-s" ] || { echo "usage: -s SERIAL [--dry-run] [--verify]"; exit 1; }
S="${2:?usage: -s SERIAL}"
DRY=""; VERIFY=""
for a in "$@"; do case "$a" in --dry-run) DRY=1;; --verify) VERIFY=1;; esac; done

mkdir -p logs
LOGF="logs/run-${S}-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOGF") 2>&1

A(){ adb -s "$S" shell "$1" 2>/dev/null | tr -d '\r'; }
die(){ echo "ABORT: $*"; echo "SUPPORT SUMMARY: run aborted — share $LOGF and migration.jsonl with LibreEcho support."; exit 1; }
sha(){ sha256sum "$1" | cut -d' ' -f1; }
log(){ printf '{"ts":"%s","serial":"%s","event":"%s","sha256":"%s"}\n' "$(date -u +%FT%TZ)" "$S" "$1" "${2:-}" >> migration.jsonl; }

echo "== radar-kaeru migration run =="
echo "ts: $(date -u +%FT%TZ)  serial: $S  mode: ${DRY:+dry-run}${VERIFY:+verify}"
echo "host: $(uname -a)"
fastboot --version 2>/dev/null | head -1
adb --version 2>&1 | head -1
A "uname -a; id"
adb -s "$S" get-state | grep -q device || die "no ADB device $S"

# ---- gate 1: chain identity (standing rule: identify chain BEFORE touching expdb)
expdb_hex=$(A "dd if=/dev/mmcblk0p7 bs=64 count=1 2>/dev/null | od -An -tx1 | tr -d ' \\n'")
kaeru_hex=$(head -c 64 stage/radar-kaeru.bin | od -An -tx1 | tr -d ' \\n')
marker_hex=$(printf 'FASTBOOT_PLEASE' | od -An -tx1 | tr -d ' \\n')
case "$expdb_hex" in
  8816885840*) echo "expdb already holds a kaeru payload (magic 88 16 88 58 40) — chain ALREADY converted; refusing."
     [ "${ALLOW_CONVERTED:-}" = 1 ] || die "already converted" ;;
  "$marker_hex"*) echo "expdb holds legacy FASTBOOT_PLEASE marker — old chain, safe to write kaeru" ;;
  0000000000000000*) echo "expdb is zeroed — old chain, no marker, safe to write kaeru" ;;
  *) echo "expdb head: $expdb_hex — unrecognized; refusing."
     A "dd if=/dev/mmcblk0p7 bs=64 count=1 2>/dev/null | od -An -c | head -2"
     die "expdb not legacy-marker/zeros/kaeru; refusing to touch it" ;;
esac
lk_str=$(A "strings /dev/mmcblk0p3 2>/dev/null | grep -iE 'biscuit|radar' | head -2")
echo "current lk_a identity strings: ${lk_str:-none}"
case "$lk_str" in *iscuit*) : ;; *) echo "WARN: lk_a does not contain 'biscuit' — confirm this device really is on the old biscuit chain before proceeding" ;; esac

# ---- gate 2: `_x` layout geometry (LibreEcho radar contract)
geo=$(A "grep -E 'mmcblk0p(3|4|5|6|7|10|11|16)$' /proc/partitions | tr -s ' ' | cut -d' ' -f4,5")
want="1024 mmcblk0p3
5120 mmcblk0p4
1024 mmcblk0p5
5120 mmcblk0p6
10240 mmcblk0p7
16384 mmcblk0p10
16384 mmcblk0p11
1068544 mmcblk0p16"
[ "$geo" = "$want" ] || { echo "geometry mismatch (sectors):"; echo "have:"; echo "$geo"; echo "want:"; echo "$want"; die "layout is not the legacy _x radar contract" ; }

# ---- backups (boot0, lk_a, lk_b, tee1, tee2, expdb) — capture, pull, sha-match
A 'mkdir -p /data/.kaeru'
for e in boot0:179:8:mmcblk0boot0:1048576 p3:::mmcblk0p3:1048576 p5:::mmcblk0p5:1048576 p4:::mmcblk0p4:5242880 p6:::mmcblk0p6:5242880 p7:::mmcblk0p7:10485760; do
  IFS=: read -r tag _ _ dev size <<<"$e"
  [ "$tag" = boot0 ] && A 'rm -f /dev/bk-b0; mknod /dev/bk-b0 b 179 8; echo 0 > /sys/block/mmcblk0boot0/force_ro'
  src=/dev/bk-b0; [ "$tag" = boot0 ] || src=/dev/$dev
  A "dd if=$src of=/data/.kaeru/$tag.img bs=4096 conv=fsync 2>&1; sync" >/dev/null || die "backup capture $tag"
  adb -s "$S" pull /data/.kaeru/$tag.img backup/$tag.img >/dev/null 2>&1 || die "pull backup $tag"
  d=$(A "sha256sum /data/.kaeru/$tag.img" | cut -d' ' -f1); h=$(sha backup/$tag.img)
  [ -n "$h" ] && [ "$h" = "$d" ] || die "backup sha mismatch $tag"
  echo "backed up $tag ${h:0:12}"; log "backup_$tag" "$h"
done
sha256sum backup/*.img > backup/MANIFEST.sha256
A 'echo 1 > /sys/block/mmcblk0boot0/force_ro'
[ -n "${DRY:-}" ] && { echo "DRY RUN complete: gates passed, backups captured and verified. No writes."; exit 0; }

# ---- stage sources, host/device sha vs pinned manifest
for f in preloader.img lk.bin tz.img tee-payload.bin radar-kaeru.bin; do
  adb -s "$S" push "stage/$f" /data/.kaeru/$f >/dev/null || die "push $f"
  h=$(sha "stage/$f"); d=$(A "sha256sum /data/.kaeru/$f" | cut -d' ' -f1)
  [ "$h" = "$d" ] || die "stage hash $f"
  grep -q "^$h" MANIFEST.sha256 || die "stage $f not in pinned MANIFEST.sha256"
  echo "staged $f ${h:0:12}"
done

# ---- writes: lk first, preloader LAST (proven order from flash.sh)
w(){ img=$1; dev=$2; size=$(stat -c%s "stage/$img.img")
  echo "== $img -> $dev"
  A "dd if=/data/.kaeru/$img.img of=$dev bs=4096 conv=fsync 2>&1; sync" >/dev/null || die "write $img"
  want=$(sha "stage/$img.img")
  got=$(A "dd if=$dev bs=4096 2>/dev/null | head -c $size | sha256sum")
  [ "${want:0:12}" = "${got:0:12}" ] || die "readback $img want ${want:0:12} got ${got:0:12}"
  echo "   readback OK ${want:0:12}"; log "write_$img" "$want"; }
w lk.bin /dev/mmcblk0p3; w lk.bin /dev/mmcblk0p5
w tee-payload.bin /dev/mmcblk0p4; w tz.img /dev/mmcblk0p6
w radar-kaeru.bin /dev/mmcblk0p7
A 'rm -f /dev/bk-b0; mknod /dev/bk-b0 b 179 8; echo 0 > /sys/block/mmcblk0boot0/force_ro'
w preloader.img /dev/bk-b0
A 'echo 1 > /sys/block/mmcblk0boot0/force_ro; cat /sys/block/mmcblk0boot0/force_ro; rm -f /dev/bk-b0; rm -rf /data/.kaeru; sync'
echo ALLDONE
echo "SUPPORT SUMMARY: serial $S converted old-chain -> kaeru radar chain."
echo "  log: $LOGF   structured: migration.jsonl   backups: backup/ (+ backup/MANIFEST.sha256)"
echo "  Share $LOGF and migration.jsonl with LibreEcho support if anything looks wrong."

# ---- optional verify: reboot to fastboot, expect product RADAR + unlocked
[ -n "${VERIFY:-}" ] || exit 0
adb -s "$S" reboot-bootloader >/dev/null; sleep 8
for g in product unlock_status lk_build_desc pl_build_desc; do
  echo "-- getvar $g"; fastboot -s "$S" getvar "$g" 2>&1 || true
done
fastboot -s "$S" getvar product 2>&1 | grep -q 'RADAR' || die "product not RADAR after conversion (see recovery net in README)"
fastboot -s "$S" reboot >/dev/null
echo "VERIFY OK: product=RADAR, rebooting to LibreEcho"
echo "SUPPORT SUMMARY: serial $S verified post-conversion (getvar block above)."
