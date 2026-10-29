#!/bin/bash
# radar-kaeru-migration: convert a radar_puffin running the OLD biscuit/k32 amonet
# chain to the current kaeru-based radar amonet chain (radar v1.0.0 package).
#
# Chain swap (Plan 1 from radar-rechain-plan.md) PLUS a GPT conversion step: the
# kaeru LK resolves the partition NAME boot_a and has NO `_x` redirect, so a unit
# left on the legacy `_x` layout flashes/boots the 110 MiB wrapper (p17) while
# bootctl/OTA/installer read p10 -> one-shot dies with "boot_a_x readback hash
# mismatch". The GPT step (gpt-fix.py) renames boot_a_x/boot_b_x -> boot_a/boot_b
# (same sectors, so the OS image stays) and drops the p17/p18 wrappers. userdata is
# NOT extended (unlike upstream restore_gpt): it stays at its legacy size
# (2137088 or 2153472 sectors), both of which the installer accepts.
# Not written: misc, recovery, userdata contents.
#
# Proven write/readback pattern copied from ../radar-full-stock-20261009/flash.sh.
# Chain-version gate before touching expdb per standing rule (2026-09-24):
# never erase/write expdb without first identifying the chain on the device.
#
# Usage: ./migrate.sh -s SERIAL [--dry-run] [--verify] [--gpt-fix] [--gpt-restore] [--no-gpt-fix]
#   --dry-run     : gates + backups only, no writes
#   --verify      : after writes, reboot to fastboot and check getvar product/unlock
#   --gpt-fix     : ONLY convert the `_x` GPT to the plain layout. For a unit whose chain
#                   is already kaeru (expdb magic) but which still has `_x` (i.e. migrated
#                   with an older copy of this kit). Run with --dry-run first.
#   --gpt-restore : write the saved original GPT (backup/gpt-*-orig.bin) back.
#   --no-gpt-fix  : full migration without the GPT step (old behaviour; leaves `_x`).
set -u
cd "$(dirname "$0")"

[ "${1:-}" = "-s" ] || { echo "usage: -s SERIAL [--dry-run] [--verify]"; exit 1; }
S="${2:?usage: -s SERIAL}"
DRY=""; VERIFY=""; GPTFIX=""; GPTRESTORE=""; NOGPT=""
for a in "$@"; do case "$a" in --dry-run) DRY=1;; --verify) VERIFY=1;; --gpt-fix) GPTFIX=1;; --gpt-restore) GPTRESTORE=1;; --no-gpt-fix) NOGPT=1;; esac; done

mkdir -p logs backup
LOGF="logs/run-${S}-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOGF") 2>&1

A(){ adb -s "$S" shell "$1" 2>/dev/null | tr -d '\r'; }
die(){ echo "ABORT: $*"; echo "SUPPORT SUMMARY: run aborted — share $LOGF and migration.jsonl with LibreEcho support."; exit 1; }
sha(){ sha256sum "$1" | cut -d' ' -f1; }
log(){ printf '{"ts":"%s","serial":"%s","event":"%s","sha256":"%s"}\n' "$(date -u +%FT%TZ)" "$S" "$1" "${2:-}" >> migration.jsonl; }

echo "== radar-kaeru migration run =="
echo "ts: $(date -u +%FT%TZ)  serial: $S  mode: ${DRY:+dry-run }${VERIFY:+verify }${GPTFIX:+gpt-fix }${GPTRESTORE:+gpt-restore }${NOGPT:+no-gpt-fix}"
echo "host: $(uname -a)"
fastboot --version 2>/dev/null | head -1
adb --version 2>&1 | head -1
A "uname -a; id"
adb -s "$S" get-state | grep -q device || die "no ADB device $S"

# ---- GPT step: `_x` -> plain layout (see header). Honours $DRY. The backup GPT is
# written first and the primary LAST, so an interruption leaves the old valid primary.
gpt_write(){ # $1=primary file (34 sectors)  $2=backup file (33 sectors), host paths
  local N bs bd ps pd
  N=$(A 'blockdev --getsz /dev/mmcblk0'); [ -n "$N" ] || die "cannot read disk size"
  A 'mkdir -p /data/.kaeru'
  adb -s "$S" push "$2" /data/.kaeru/gpt-backup-new.bin >/dev/null || die "push backup GPT"
  adb -s "$S" push "$1" /data/.kaeru/gpt-primary-new.bin >/dev/null || die "push primary GPT"
  A "dd if=/data/.kaeru/gpt-backup-new.bin of=/dev/mmcblk0 bs=512 seek=$((N-33)) conv=notrunc,fsync 2>&1; sync" >/dev/null || die "write backup GPT"
  bs=$(sha "$2"); bd=$(A "dd if=/dev/mmcblk0 bs=512 skip=$((N-33)) count=33 2>/dev/null | sha256sum" | cut -d' ' -f1)
  [ "$bs" = "$bd" ] || die "backup GPT readback mismatch (want ${bs:0:12} got ${bd:0:12}); primary NOT touched, device still boots"
  echo "   backup GPT written + readback OK ${bs:0:12}"
  A "dd if=/data/.kaeru/gpt-primary-new.bin of=/dev/mmcblk0 bs=512 seek=0 count=34 conv=notrunc,fsync 2>&1; sync" >/dev/null || die "write primary GPT"
  ps=$(sha "$1"); pd=$(A "dd if=/dev/mmcblk0 bs=512 count=34 2>/dev/null | sha256sum" | cut -d' ' -f1)
  [ "$ps" = "$pd" ] || die "primary GPT readback mismatch (want ${ps:0:12} got ${pd:0:12}) - DO NOT REBOOT; run --gpt-restore"
  echo "   primary GPT written + readback OK ${ps:0:12}"
  A 'rm -f /data/.kaeru/gpt-*-new.bin; sync'
  log "gpt_write" "$ps"
}
gpt_step(){
  local N rc t
  command -v python3 >/dev/null || die "python3 is required on the host for the GPT step"
  [ -f gpt-fix.py ] || die "gpt-fix.py missing"
  grep -q "^$(sha gpt-fix.py)" MANIFEST.sha256 || die "gpt-fix.py is not in the pinned MANIFEST.sha256"
  echo "== GPT step: p10 $(A 'grep PARTNAME /sys/class/block/mmcblk0p10/uevent')"
  N=$(A 'blockdev --getsz /dev/mmcblk0'); [ -n "$N" ] || die "cannot read disk size"
  mkdir -p backup gpt-work; A 'mkdir -p /data/.kaeru'
  A "dd if=/dev/mmcblk0 of=/data/.kaeru/gpt-primary.bin bs=512 count=34 2>/dev/null; dd if=/dev/mmcblk0 of=/data/.kaeru/gpt-backup.bin bs=512 skip=$((N-33)) count=33 2>/dev/null; sync" >/dev/null
  adb -s "$S" pull /data/.kaeru/gpt-primary.bin gpt-work/primary.bin >/dev/null 2>&1 || die "pull primary GPT"
  adb -s "$S" pull /data/.kaeru/gpt-backup.bin gpt-work/backup.bin >/dev/null 2>&1 || die "pull backup GPT"
  for t in primary backup; do
    [ "$(sha gpt-work/$t.bin)" = "$(A "sha256sum /data/.kaeru/gpt-$t.bin" | cut -d' ' -f1)" ] || die "GPT $t pull hash mismatch"
  done
  python3 gpt-fix.py gpt-work/primary.bin gpt-work/backup.bin gpt-work/primary.new gpt-work/backup.new --show; rc=$?
  [ $rc -eq 3 ] && { echo "GPT already plain - nothing to do."; log "gpt_already_plain" ""; return 0; }
  [ $rc -eq 0 ] || die "gpt-fix.py refused (see above); nothing was written"
  [ -f backup/gpt-primary-orig.bin ] || { cp gpt-work/primary.bin backup/gpt-primary-orig.bin; cp gpt-work/backup.bin backup/gpt-backup-orig.bin; }
  ( cd backup && sha256sum gpt-*-orig.bin > GPT-MANIFEST.sha256 )
  log "gpt_backup_primary" "$(sha backup/gpt-primary-orig.bin)"
  [ -n "${DRY:-}" ] && { echo "DRY RUN: GPT conversion validated, nothing written."; return 0; }
  gpt_write gpt-work/primary.new gpt-work/backup.new
  echo "GPT converted. Reboot once (adb reboot) so the kernel/LK reread it, then rerun the one-shot installer with --reinstall."
  echo "Roll back with: ./migrate.sh -s $S --gpt-restore"
}

if [ -n "$GPTRESTORE" ]; then
  [ -f backup/gpt-primary-orig.bin ] && [ -f backup/gpt-backup-orig.bin ] || die "no saved GPT in backup/"
  ( cd backup && sha256sum -c GPT-MANIFEST.sha256 >/dev/null ) || die "saved GPT fails backup/GPT-MANIFEST.sha256"
  [ -n "$DRY" ] && { echo "DRY RUN: would restore saved GPT."; exit 0; }
  gpt_write backup/gpt-primary-orig.bin backup/gpt-backup-orig.bin
  echo "GPT restored. Reboot (adb reboot)."; exit 0
fi

# ---- gate 1: chain identity (standing rule: identify chain BEFORE touching expdb)
expdb_hex=$(A "dd if=/dev/mmcblk0p7 bs=64 count=1 2>/dev/null | od -An -tx1 | tr -d ' \\n'")
kaeru_hex=$(head -c 64 stage/radar-kaeru.bin | od -An -tx1 | tr -d ' \\n')
marker_hex=$(printf 'FASTBOOT_PLEASE' | od -An -tx1 | tr -d ' \\n')
case "$expdb_hex" in
  8816885840*) echo "expdb already holds a kaeru payload (magic 88 16 88 58 40) — chain ALREADY converted."
     [ -n "$GPTFIX" ] || [ "${ALLOW_CONVERTED:-}" = 1 ] || die "already converted (if p10 is still named boot_a_x, run again with --gpt-fix)" ;;
  "$marker_hex"*) [ -z "$GPTFIX" ] || die "--gpt-fix needs a kaeru chain; the old biscuit LK requires the _x layout. Run the full migration instead."
     echo "expdb holds legacy FASTBOOT_PLEASE marker — old chain, safe to write kaeru" ;;
  0000000000000000*) [ -z "$GPTFIX" ] || die "--gpt-fix needs a kaeru chain; the old biscuit LK requires the _x layout. Run the full migration instead."
     echo "expdb is zeroed — old chain, no marker, safe to write kaeru" ;;
  *) echo "expdb head: $expdb_hex — unrecognized; refusing."
     A "dd if=/dev/mmcblk0p7 bs=64 count=1 2>/dev/null | od -An -c | head -2"
     die "expdb not legacy-marker/zeros/kaeru; refusing to touch it" ;;
esac
lk_str=$(A "strings /dev/mmcblk0p3 2>/dev/null | grep -iE 'biscuit|radar' | head -2")
echo "current lk_a identity strings: ${lk_str:-none}"
[ -n "$GPTFIX" ] || case "$lk_str" in *iscuit*) : ;; *) echo "WARN: lk_a does not contain 'biscuit' — confirm this device really is on the old biscuit chain before proceeding" ;; esac

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
# Old-chain units carry the legacy amonet userdata of either GPT: 1068544 KiB
# (radar) or 1076736 KiB (biscuit, the chain that reports product=BISCUIT).
case "$geo" in *"
1076736 mmcblk0p16") want="${want%1068544 mmcblk0p16}1076736 mmcblk0p16" ;; esac
[ "$geo" = "$want" ] || { echo "geometry mismatch (sectors):"; echo "have:"; echo "$geo"; echo "want:"; echo "$want"; die "layout is not the legacy _x radar contract" ; }

if [ -n "$GPTFIX" ]; then gpt_step; echo ALLDONE; echo "SUPPORT SUMMARY: serial $S gpt-fix ${DRY:+dry-run }complete. log: $LOGF"; exit 0; fi

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
[ -n "${DRY:-}" ] && { [ -n "$NOGPT" ] || gpt_step; echo "DRY RUN complete: gates passed, backups captured and verified. No writes."; exit 0; }

# ---- stage sources, host/device sha vs pinned manifest
for f in preloader.img lk.bin tz.img tee-payload.bin radar-kaeru.bin; do
  adb -s "$S" push "stage/$f" /data/.kaeru/$f >/dev/null || die "push $f"
  h=$(sha "stage/$f"); d=$(A "sha256sum /data/.kaeru/$f" | cut -d' ' -f1)
  [ "$h" = "$d" ] || die "stage hash $f"
  grep -q "^$h" MANIFEST.sha256 || die "stage $f not in pinned MANIFEST.sha256"
  echo "staged $f ${h:0:12}"
done

# ---- writes: lk first, preloader LAST (proven order from flash.sh)
w(){ img=$1; dev=$2; size=$(stat -c%s "stage/$img") || die "missing stage/$img"
  echo "== $img -> $dev"
  A "dd if=/data/.kaeru/$img of=$dev bs=4096 conv=fsync 2>&1; sync" >/dev/null || die "write $img"
  want=$(sha "stage/$img")
  got=$(A "dd if=$dev bs=4096 2>/dev/null | head -c $size | sha256sum")
  [ "${want:0:12}" = "${got:0:12}" ] || die "readback $img want ${want:0:12} got ${got:0:12}"
  echo "   readback OK ${want:0:12}"; log "write_$img" "$want"; }
w lk.bin /dev/mmcblk0p3; w lk.bin /dev/mmcblk0p5
w tee-payload.bin /dev/mmcblk0p4; w tz.img /dev/mmcblk0p6
w radar-kaeru.bin /dev/mmcblk0p7
A 'rm -f /dev/bk-b0; mknod /dev/bk-b0 b 179 8; echo 0 > /sys/block/mmcblk0boot0/force_ro'
w preloader.img /dev/bk-b0
A 'echo 1 > /sys/block/mmcblk0boot0/force_ro; cat /sys/block/mmcblk0boot0/force_ro; rm -f /dev/bk-b0; rm -rf /data/.kaeru; sync'
[ -n "$NOGPT" ] || { echo "== chain written; converting GPT (if this step fails, rerun with --gpt-fix before rebooting)"; gpt_step; }
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
