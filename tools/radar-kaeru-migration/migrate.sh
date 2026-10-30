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
# Not written: recovery, userdata contents. misc gets only amonet's force-fastboot
# marker (misc[0:16] = "FASTBOOT_PLEASE\0", as amonet's force_fastboot()); the BCB at
# misc 0x360 is never touched.
#
# Reboot safety (issue #325): the old LibreEcho OS turns `adb reboot bootloader` /
# `adb reboot fastboot` into a FASTBOOT_PLEASE write to expdb, which is exactly where
# the kaeru payload lives, and the unit then loops in USBDL ("Failed to load LK").
# So, before the first write this kit blocks that path (/tmp/reboot.request made a
# directory) and refuses images that write expdb at boot (development profile, kernel
# echo-marker). After the writes it re-checks the expdb LK -> kaeru chain, makes expdb
# read-only for the rest of this boot, sets the misc force-fastboot marker so kaeru
# stops in fastboot instead of booting the old OS again, and reboots ONLY with
# `reboot -f` (the reboot syscall; no init or adbd reboot handler runs).
# This script must never use adb's or fastboot's own reboot commands.
#
# Proven write/readback pattern copied from ../radar-full-stock-20261009/flash.sh.
# Chain-version gate before touching expdb per standing rule (2026-09-24):
# never erase/write expdb without first identifying the chain on the device.
#
# Usage: ./migrate.sh -s SERIAL [--dry-run] [--verify] [--gpt-fix] [--gpt-restore] [--no-gpt-fix]
#   --dry-run     : gates + backups only, no writes, no reboot
#   --verify      : in a write run: after the guarded reboot, wait for kaeru fastboot and
#                   check product=RADAR and unlocked. On its own, against a unit already in
#                   fastboot: the same read-only check (never reboots anything).
#   --gpt-fix     : ONLY convert the `_x` GPT to the plain layout. For a unit whose chain
#                   is already kaeru (expdb magic) but which still has `_x` (i.e. migrated
#                   with an older copy of this kit). Run with --dry-run first. Ends with the
#                   same guarded reboot into kaeru fastboot.
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

WRITTEN=""; GUARDED=""
A(){ adb -s "$S" shell "$1" 2>/dev/null | tr -d '\r'; }
die(){ echo "ABORT: $*"
  if [ -n "$WRITTEN" ]; then
    echo "!! Flash has already been written on this unit. Do NOT reboot it, do NOT power it off, and do NOT"
    echo "!! use adb or fastboot reboot commands on it. Leave it running and share the log with LibreEcho support."
  elif [ -n "$GUARDED" ]; then
    echo "NOTE: nothing was written to the boot chain. The reboot guard stays until the next boot, so adb reboot"
    echo "      requests fail with 'File exists'; unplugging and replugging power is safe on this unit."
  fi
  echo "SUPPORT SUMMARY: run aborted — share $LOGF and migration.jsonl with LibreEcho support."; exit 1; }
sha(){ sha256sum "$1" | cut -d' ' -f1; }
log(){ printf '{"ts":"%s","serial":"%s","event":"%s","sha256":"%s"}\n' "$(date -u +%FT%TZ)" "$S" "$1" "${2:-}" >> migration.jsonl; }
bounded(){ if command -v timeout >/dev/null 2>&1; then timeout "$@"; else shift; "$@"; fi; }
le32(){ echo $((16#${1:6:2}${1:4:2}${1:2:2}${1:0:2})); }

# ---- kaeru chain identity, as the installer's _expdb_kaeru_chain_ok (libreecho-install.py):
# expdb[0] is a non-extended MTK header named "LK"; the next header, at
# round8(512 + LK data size), is named "kaeru" and carries the extended magic.
LK_MAGIC=88168858; LK_EXT_MAGIC=89168958
hdr_at(){ A "dd if=/dev/$EXPDB_NODE bs=8 skip=$(($1 / 8)) count=10 2>/dev/null | od -An -v -tx1 | tr -d ' \\n'"; }
kaeru_hdr_at(){ local h; h=$(hdr_at "$1")
  [ ${#h} -eq 160 ] && [ "${h:0:8}" = $LK_MAGIC ] && [ "${h:16:12}" = 6b6165727500 ] && [ "${h:96:8}" = $LK_EXT_MAGIC ]; }
expdb_chain_ok(){ local h off; h=$(hdr_at 0)
  [ ${#h} -eq 160 ] && [ "${h:0:8}" = $LK_MAGIC ] && [ "${h:16:6}" = 4c4b00 ] && [ "${h:96:8}" != $LK_EXT_MAGIC ] || return 1
  off=$((512 + $(le32 "${h:8:8}"))); off=$((off + (8 - off % 8) % 8))
  kaeru_hdr_at "$off"; }
# Where the pinned payload puts its kaeru header (the LK header at 0 may be destroyed).
pinned_kaeru_offset(){ local h off; h=$(head -c 8 stage/radar-kaeru.bin | od -An -v -tx1 | tr -d ' \n')
  off=$((512 + $(le32 "${h:8:8}"))); echo $((off + (8 - off % 8) % 8)); }

# Exactly one block device must carry PARTNAME=$1 and it must be the expected node.
part_node(){ local n
  n=$(A "grep -l '^PARTNAME=$1\$' /sys/class/block/*/uevent" | sed -n 's#^/sys/class/block/\([A-Za-z0-9]*\)/uevent$#\1#p')
  [ "$(printf '%s\n' "$n" | grep -c .)" = 1 ] && [ "$n" = "$2" ] || die "PARTNAME=$1 does not resolve to exactly $2 (got: ${n:-none})"
  echo "PARTNAME=$1 -> $n"; }

# ---- image preflight: who can write expdb on the running OS (runs in every mode)
preflight_image(){
  local probe out
  probe='P=/etc/libreecho/image-profile; T=/etc/libreecho/target; R=/tmp/reboot.request;'
  probe="$probe"' if [ -f $P ]; then echo "profile=$(head -n1 $P)"; else echo profile=missing; fi;'
  probe="$probe"' if [ -L $T ]; then echo target=invalid; elif [ -f $T ]; then echo "target=$(grep -c "^target_id=" $T):$(sed -n "s/^target_id=//p" $T | head -n1)"; elif [ -e $T ]; then echo target=invalid; else echo target=absent; fi;'
  probe="$probe"' if [ -L $R ]; then echo request=other; elif [ -d $R ]; then echo request=dir; elif [ -e $R ]; then echo request=file; else echo request=none; fi;'
  probe="$probe"' echo "init_markers=$(grep -c FASTBOOT_PLEASE /init 2>/dev/null)";'
  probe="$probe"' echo "kmarker_dmesg=$(dmesg 2>/dev/null | grep -c echo-marker)";'
  probe="$probe"' echo "kmarker_syms=$(grep -c -w echo_fastboot_marker_init /proc/kallsyms 2>/dev/null)"'
  out=$(A "$probe")
  echo "== image preflight"; echo "$out" | sed 's/^/   /'
  v(){ printf '%s\n' "$out" | sed -n "s/^$1=//p" | head -n1; }
  case "$(v profile)" in
    ota) : ;;
    development) die "this is a DEVELOPMENT-profile LibreEcho image. It writes FASTBOOT_PLEASE to expdb at every boot, which would destroy the kaeru chain. This kit must not be used on development images: install a release (OTA-profile) image on the old chain first." ;;
    *) die "cannot confirm the image profile (/etc/libreecho/image-profile = '$(v profile)'); only OTA-profile release images are supported" ;;
  esac
  case "$(v target)" in
    1:radar_puffin) echo "   image target: radar_puffin" ;;
    absent) echo "   image target: no /etc/libreecho/target (pre-0.14 image). Those images were built for radar_puffin only, as LibreEcho's own init assumes; the radar _x geometry gate below still applies." ;;
    *) die "image target is not radar_puffin (/etc/libreecho/target: '$(v target)'); this kit is for the Echo 2nd Gen (radar_puffin) only" ;;
  esac
  case "$(v request)" in
    none|dir) : ;;
    *) die "/tmp/reboot.request already exists and is not the kit's guard directory ($(v request)): a reboot request is pending on this unit. Do not continue: unplug and replug its power (do not use adb reboot), wait for LibreEcho, then run the dry run again." ;;
  esac
  [ "$(v kmarker_dmesg)" = 0 ] || [ -z "$(v kmarker_dmesg)" ] || die "the running kernel logged echo-marker writes (it writes FASTBOOT_PLEASE to expdb at boot); refusing"
  [ "$(v kmarker_syms)" = 0 ] || [ -z "$(v kmarker_syms)" ] || die "the running kernel contains the echo-marker expdb writer (CONFIG_LIBREECHO_DEV_RECOVERY_MARKER); refusing"
  [ -n "$(v kmarker_syms)" ] || echo "   NOTE: /proc/kallsyms unreadable; kernel writer checked from dmesg only"
  echo "   init FASTBOOT_PLEASE writers: $(v init_markers) (reboot-request path; blocked by the guard before any write)"
  log "preflight_profile_ota" ""
}

# ---- reboot guard: a DIRECTORY at /tmp/reboot.request makes adbd's reboot shim fail
# (O_CREAT|O_EXCL -> EEXIST) and the init reboot loop ignore it ([ -f ] is false), so
# nothing on the running OS can turn a reboot request into an expdb write.
guard_reboot_window(){
  [ "$(A 'mkdir /tmp/reboot.request 2>/dev/null; [ -d /tmp/reboot.request ] && [ ! -L /tmp/reboot.request ] && echo GUARDED')" = GUARDED ] \
    || die "could not create the /tmp/reboot.request guard directory; nothing was written"
  GUARDED=1
  echo "reboot guard in place: /tmp/reboot.request is a directory (adb reboot requests now fail with 'File exists' until the next boot)"
  log "reboot_guard" ""
}

backup_part(){ # $1=tag  $2=device node (under /dev)  $3=host file
  A 'mkdir -p /data/.kaeru'
  A "dd if=/dev/$2 of=/data/.kaeru/$1.img bs=4096 conv=fsync 2>&1; sync" >/dev/null || die "backup capture $1"
  adb -s "$S" pull /data/.kaeru/$1.img "$3" >/dev/null 2>&1 || die "pull backup $1"
  d=$(A "sha256sum /data/.kaeru/$1.img" | cut -d' ' -f1); h=$(sha "$3")
  [ -n "$h" ] && [ "$h" = "$d" ] || die "backup sha mismatch $1"
  echo "backed up $1 ${h:0:12}"; log "backup_$1" "$h"
}

next_steps(){
  echo "NEXT STEP (unit is now in kaeru fastboot — keep it there):"
  echo "  run the one-shot installer from fastboot with a NEW install id, for example:"
  echo "    ./run-one-shot.sh \"\$TAG\" --reinstall --target radar_puffin --install-id <new-name> --fastboot-serial $S --execute-hardware"
  echo "  The installer sees the unit already in fastboot and does not reboot it first."
  echo "  Do NOT boot the old LibreEcho on this unit and do NOT use adb's reboot-to-bootloader or reboot-to-fastboot commands on it:"
  echo "  the old OS writes FASTBOOT_PLEASE over the kaeru payload in expdb and the unit stops booting (issue #325)."
}

fastboot_check(){ # read-only; never reboots
  local p u
  for g in product unlock_status lk_build_desc pl_build_desc; do
    echo "-- getvar $g"; bounded 20 fastboot -s "$S" getvar "$g" 2>&1 || true
  done
  p=$(bounded 20 fastboot -s "$S" getvar product 2>&1 | sed -n 's/^product: *//p' | head -n1)
  u=$(bounded 20 fastboot -s "$S" getvar unlock_status 2>&1 | sed -n 's/^unlock_status: *//p' | head -n1 | tr 'A-Z' 'a-z')
  [ "$p" = RADAR ] || die "product is '${p:-none}', not RADAR (see Recovery in README.md; do not boot the old OS)"
  case "$u" in true|unlocked|yes|1) : ;; *) die "bootloader reports unlock_status '${u:-none}', not unlocked" ;; esac
  echo "VERIFY OK: product=RADAR, unlocked. The unit stays in fastboot."
  log "verify_ok" ""
}

wait_fastboot(){
  local i
  for i in $(seq 1 60); do
    bounded 10 fastboot devices 2>/dev/null | grep -q "^$S[[:space:]]" && return 0
    sleep 2
  done
  return 1
}

# ---- the only way this kit reboots the unit: chain re-verify -> expdb read-only ->
# misc force-fastboot marker -> `reboot -f`.
safe_finish(){ # $1 = kaeru | any
  local ro misc_before misc_after mh want
  if expdb_chain_ok; then
    echo "expdb re-verified: LK header at 0, kaeru header chained after it"; log "expdb_chain_verified" ""
  elif [ "$1" = kaeru ]; then
    die "expdb does not hold a valid LK -> kaeru chain after the writes"
  else
    echo "expdb holds no kaeru chain (old chain): no expdb lock or misc marker needed"
  fi
  if [ "$1" = kaeru ] || expdb_chain_ok; then
    ro=$(A "blockdev --setro /dev/$EXPDB_NODE 2>/dev/null; blockdev --getro /dev/$EXPDB_NODE 2>/dev/null")
    if [ "$ro" = 1 ]; then echo "expdb is read-only until the next boot (blockdev --getro = 1)"; log "expdb_setro" ""
    else echo "WARN: could not confirm expdb read-only (blockdev --getro = '${ro:-none}'); relying on the reboot guard"; fi
    [ "$(A '[ -d /tmp/reboot.request ] && [ ! -L /tmp/reboot.request ] && echo GUARDED')" = GUARDED ] \
      || die "the /tmp/reboot.request guard directory is gone; refusing to reboot"
    # misc force-fastboot marker, exactly amonet common force_fastboot(): misc[0:16].
    mkdir -p gpt-work; printf 'FASTBOOT_PLEASE\0' > gpt-work/misc-marker.bin
    want=$(od -An -v -tx1 gpt-work/misc-marker.bin | tr -d ' \n')
    adb -s "$S" push gpt-work/misc-marker.bin /tmp/kaeru-misc-marker.bin >/dev/null || die "push misc marker"
    [ "$(A 'sha256sum /tmp/kaeru-misc-marker.bin' | cut -d' ' -f1)" = "$(sha gpt-work/misc-marker.bin)" ] || die "misc marker push hash mismatch"
    misc_before=$(A "dd if=/dev/$MISC_NODE bs=16 skip=1 2>/dev/null | sha256sum" | cut -d' ' -f1)
    A "dd if=/tmp/kaeru-misc-marker.bin of=/dev/$MISC_NODE bs=16 count=1 conv=notrunc,fsync 2>&1; sync" >/dev/null || die "write misc marker"
    mh=$(A "dd if=/dev/$MISC_NODE bs=16 count=1 2>/dev/null | od -An -v -tx1 | tr -d ' \\n'")
    misc_after=$(A "dd if=/dev/$MISC_NODE bs=16 skip=1 2>/dev/null | sha256sum" | cut -d' ' -f1)
    [ "$mh" = "$want" ] || die "misc marker readback mismatch (got ${mh:-none})"
    [ -n "$misc_before" ] && [ "$misc_before" = "$misc_after" ] || die "misc changed beyond its first 16 bytes"
    A 'rm -f /tmp/kaeru-misc-marker.bin'
    echo "misc force-fastboot marker written + readback OK (misc[0:16] only; BCB untouched)"; log "misc_marker" "$(sha gpt-work/misc-marker.bin)"
    expdb_chain_ok || die "expdb chain changed before the reboot"
  fi
  echo "== rebooting with the reboot syscall (reboot -f); kaeru should stop in fastboot"
  log "reboot_f" ""
  bounded 20 adb -s "$S" shell 'sync; sync; reboot -f' >/dev/null 2>&1
  WRITTEN=""  # rebooted: the "leave it running" advice no longer applies
  if [ -n "${VERIFY:-}" ]; then
    wait_fastboot || die "device did not appear in fastboot within 120 s; do not power-cycle it repeatedly — see Recovery in README.md"
    fastboot_check
  fi
  [ "$1" = kaeru ] && next_steps
  return 0
}

echo "== radar-kaeru migration run =="
echo "ts: $(date -u +%FT%TZ)  serial: $S  mode: ${DRY:+dry-run }${VERIFY:+verify }${GPTFIX:+gpt-fix }${GPTRESTORE:+gpt-restore }${NOGPT:+no-gpt-fix}"
echo "host: $(uname -a)"
fastboot --version 2>/dev/null | head -1
adb --version 2>&1 | head -1

# ---- --verify on its own: read-only fastboot check of a unit that is already in fastboot.
if [ -n "$VERIFY" ] && [ -z "$GPTFIX$GPTRESTORE" ] && ! adb -s "$S" get-state 2>/dev/null | grep -q device; then
  bounded 10 fastboot devices 2>/dev/null | grep -q "^$S[[:space:]]" || die "$S is neither on ADB nor in fastboot"
  echo "== read-only fastboot check (nothing is written, nothing is rebooted)"
  fastboot_check; next_steps; exit 0
fi

A "uname -a; id"
adb -s "$S" get-state | grep -q device || die "no ADB device $S"
preflight_image
EXPDB_NODE=mmcblk0p7; MISC_NODE=mmcblk0p8
part_node expdb "$EXPDB_NODE"; part_node misc "$MISC_NODE"

# ---- GPT step: `_x` -> plain layout (see header). Honours $DRY. The backup GPT is
# written first and the primary LAST, so an interruption leaves the old valid primary.
gpt_write(){ # $1=primary file (34 sectors)  $2=backup file (33 sectors), host paths
  local N bs bd ps pd
  N=$(A 'blockdev --getsz /dev/mmcblk0'); [ -n "$N" ] || die "cannot read disk size"
  A 'mkdir -p /data/.kaeru'
  adb -s "$S" push "$2" /data/.kaeru/gpt-backup-new.bin >/dev/null || die "push backup GPT"
  adb -s "$S" push "$1" /data/.kaeru/gpt-primary-new.bin >/dev/null || die "push primary GPT"
  WRITTEN=1
  A "dd if=/data/.kaeru/gpt-backup-new.bin of=/dev/mmcblk0 bs=512 seek=$((N-33)) conv=notrunc,fsync 2>&1; sync" >/dev/null || die "write backup GPT"
  bs=$(sha "$2"); bd=$(A "dd if=/dev/mmcblk0 bs=512 skip=$((N-33)) count=33 2>/dev/null | sha256sum" | cut -d' ' -f1)
  [ "$bs" = "$bd" ] || die "backup GPT readback mismatch (want ${bs:0:12} got ${bd:0:12}); primary NOT touched"
  echo "   backup GPT written + readback OK ${bs:0:12}"
  A "dd if=/data/.kaeru/gpt-primary-new.bin of=/dev/mmcblk0 bs=512 seek=0 count=34 conv=notrunc,fsync 2>&1; sync" >/dev/null || die "write primary GPT"
  ps=$(sha "$1"); pd=$(A "dd if=/dev/mmcblk0 bs=512 count=34 2>/dev/null | sha256sum" | cut -d' ' -f1)
  [ "$ps" = "$pd" ] || die "primary GPT readback mismatch (want ${ps:0:12} got ${pd:0:12}); run --gpt-restore while the unit is still running"
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
  echo "GPT converted."
}

if [ -n "$GPTRESTORE" ]; then
  [ -f backup/gpt-primary-orig.bin ] && [ -f backup/gpt-backup-orig.bin ] || die "no saved GPT in backup/"
  ( cd backup && sha256sum -c GPT-MANIFEST.sha256 >/dev/null ) || die "saved GPT fails backup/GPT-MANIFEST.sha256"
  [ -n "$DRY" ] && { echo "DRY RUN: would restore saved GPT."; exit 0; }
  guard_reboot_window
  backup_part p8 "$MISC_NODE" "backup/p8-$(date +%Y%m%d-%H%M%S).img"
  gpt_write backup/gpt-primary-orig.bin backup/gpt-backup-orig.bin
  echo "GPT restored."
  safe_finish any; exit 0
fi

# ---- gate 1: chain identity (standing rule: identify chain BEFORE touching expdb)
expdb_hex=$(A "dd if=/dev/$EXPDB_NODE bs=64 count=1 2>/dev/null | od -An -tx1 | tr -d ' \\n'")
marker_hex=$(printf 'FASTBOOT_PLEASE' | od -An -tx1 | tr -d ' \\n')
KAERU_OFF=$(pinned_kaeru_offset)
clobbered(){
  echo "expdb head: ${expdb_hex:0:64}"
  echo "expdb holds a CLOBBERED kaeru chain: the kaeru header is still at offset $KAERU_OFF but the LK header at offset 0 is gone."
  echo "This is the issue #325 state: something on the old OS wrote over expdb after the kaeru chain was installed."
  die "clobbered kaeru chain — nothing written. Do NOT reboot or power off this unit; contact LibreEcho support (issue #325). If it already loops in USBDL ('Failed to load LK'), follow Recovery in README.md."
}
case "$expdb_hex" in
  8816885840*) expdb_chain_ok || die "expdb starts with an MTK header but is not a valid LK -> kaeru chain; refusing to touch it"
     echo "expdb already holds a kaeru payload (magic 88 16 88 58 40, LK -> kaeru chain OK) — chain ALREADY converted."
     if [ -z "$GPTFIX" ] && [ -n "$VERIFY" ]; then
       echo "This unit already has the kaeru chain and is running the old LibreEcho. Nothing was rebooted."
       echo "Do NOT use adb's reboot-to-bootloader or reboot-to-fastboot commands on it (issue #325)."
       echo "To reach kaeru fastboot safely, run: ./migrate.sh -s $S --gpt-fix --verify"
       echo "(it converts the GPT if that is still needed, then reboots through the guarded path)."
       exit 0
     fi
     [ -n "$GPTFIX" ] || [ "${ALLOW_CONVERTED:-}" = 1 ] || die "already converted (to finish safely, run again with --gpt-fix --verify)" ;;
  "$marker_hex"*) kaeru_hdr_at "$KAERU_OFF" && clobbered
     [ -z "$GPTFIX" ] || die "--gpt-fix needs a kaeru chain; the old biscuit LK requires the _x layout. Run the full migration instead."
     echo "expdb holds legacy FASTBOOT_PLEASE marker — old chain, safe to write kaeru" ;;
  0000000000000000*) kaeru_hdr_at "$KAERU_OFF" && clobbered
     [ -z "$GPTFIX" ] || die "--gpt-fix needs a kaeru chain; the old biscuit LK requires the _x layout. Run the full migration instead."
     echo "expdb is zeroed — old chain, no marker, safe to write kaeru" ;;
  *) echo "expdb head: $expdb_hex — unrecognized; refusing."
     A "dd if=/dev/$EXPDB_NODE bs=64 count=1 2>/dev/null | od -An -c | head -2"
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

if [ -n "$GPTFIX" ]; then
  [ -n "$DRY" ] || guard_reboot_window
  backup_part p8 "$MISC_NODE" "backup/p8-$(date +%Y%m%d-%H%M%S).img"
  gpt_step
  [ -n "$DRY" ] && { echo "SUPPORT SUMMARY: serial $S gpt-fix dry-run complete. log: $LOGF"; exit 0; }
  safe_finish kaeru
  echo ALLDONE; echo "SUPPORT SUMMARY: serial $S gpt-fix complete. log: $LOGF"; exit 0
fi

# ---- reboot guard BEFORE the first write of a real run (backups below write /data)
[ -n "$DRY" ] || guard_reboot_window

# ---- backups (boot0, lk_a, lk_b, tee1, tee2, expdb, misc) — capture, pull, sha-match
for e in boot0:bk-b0 p3:mmcblk0p3 p5:mmcblk0p5 p4:mmcblk0p4 p6:mmcblk0p6 p7:mmcblk0p7 p8:$MISC_NODE; do
  tag=${e%%:*}; dev=${e#*:}
  [ "$tag" = boot0 ] && A 'rm -f /dev/bk-b0; mknod /dev/bk-b0 b 179 8; echo 0 > /sys/block/mmcblk0boot0/force_ro'
  backup_part "$tag" "$dev" "backup/$tag.img"
done
sha256sum backup/boot0.img backup/p3.img backup/p4.img backup/p5.img backup/p6.img backup/p7.img backup/p8.img > backup/MANIFEST.sha256
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
  WRITTEN=1
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
[ -n "$NOGPT" ] || { echo "== chain written; converting GPT (if this step fails, rerun with --gpt-fix --verify; do not reboot by hand)"; gpt_step; }
echo "SUPPORT SUMMARY: serial $S converted old-chain -> kaeru radar chain."
echo "  log: $LOGF   structured: migration.jsonl   backups: backup/ (+ backup/MANIFEST.sha256)"
echo "  Share $LOGF and migration.jsonl with LibreEcho support if anything looks wrong."
safe_finish kaeru
echo ALLDONE
