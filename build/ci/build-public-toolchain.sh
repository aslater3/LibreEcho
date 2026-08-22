#!/usr/bin/env bash
# Build the ARM32 musl toolchain from public source into a job-local root.
set -euo pipefail
: "${RUNNER_TEMP:?set RUNNER_TEMP}"
OUT="${1:?output root}"
SRC="$RUNNER_TEMP/musl-cross-make-227df8b99103f9c59f6570babf892978e293082f"
rm -rf "$SRC" "$OUT"
command -v curl >/dev/null || {
  echo "ERROR: curl is required for public toolchain downloads" >&2
  exit 1
}
git clone --quiet --depth 1 https://github.com/richfelker/musl-cross-make.git "$SRC"
git -C "$SRC" fetch --quiet --depth 1 origin 227df8b99103f9c59f6570babf892978e293082f
git -C "$SRC" checkout --quiet 227df8b99103f9c59f6570babf892978e293082f
# Reproduce the Alpine musl SONAME contract: musl-cross-make's cowpatch
# mechanism applies patches/<pkg>/* over the extracted source, so drop the
# reviewed SONAME diff beside the CVE patches. Without it, libc.so has no
# SONAME and busybox links libc.so, which the device loader cannot resolve
# (it stages libc.musl-armv7.so.1 -> ld-musl-armhf.so.1).
script_dir="$(cd -- "$(dirname -- "$0")" && pwd -P)"
soname_patch="$script_dir/../inputs/musl-alpine-soname.diff"
[[ -f "$soname_patch" ]] || {
  echo "ERROR: musl Alpine SONAME patch is missing: $soname_patch" >&2
  exit 1
}
cp -- "$soname_patch" "$SRC/patches/musl-1.2.6/50-alpine-soname.diff"
printf '%s\n' \
  'TARGET = arm-linux-musleabihf' \
  "OUTPUT = $OUT" \
  'GNU_SITE = https://ftp.gnu.org/gnu' \
  'MUSL_SITE = https://www.musl-libc.org/releases' \
  'DL_CMD = curl -4 -L --fail --retry 5 --retry-all-errors --connect-timeout 30 --max-time 1800 -o' \
  > "$SRC/config.mak"
for attempt in 1 2 3; do
  if make -C "$SRC" -j"${JOBS:-2}"; then
    break
  fi
  if [[ "$attempt" == 3 ]]; then
    echo "ERROR: public ARM32 toolchain build failed after $attempt attempts" >&2
    exit 1
  fi
  sleep $((attempt * 10))
done
make -C "$SRC" install
mkdir -p "$OUT/usr"
rm -rf "$OUT/usr/bin" "$OUT/usr/include" "$OUT/usr/lib" "$OUT/usr/libexec"   "$OUT/usr/arm-linux-musleabihf" "$OUT/usr/armv7-alpine-linux-musleabihf"
mkdir -p "$OUT/usr/bin" "$OUT/usr/armv7-alpine-linux-musleabihf"
cp -a "$OUT/bin/." "$OUT/usr/bin/"
# GCC resolves cc1/cc1plus via ../libexec relative to the driver, so the
# usr/bin compiler copies need the libexec tree beside them.
cp -a "$OUT/libexec" "$OUT/usr/libexec"
cp -a "$OUT/arm-linux-musleabihf/include" "$OUT/usr/include"
cp -a "$OUT/arm-linux-musleabihf/lib" "$OUT/usr/lib"
# GCC resolves its internal target include/include-fixed and libgcc through
# <prefix>/lib/gcc/<configured-target>/<ver>/; stage that tree beside the
# usr/bin drivers once usr/lib exists (missing it yields 'no include path
# for stdc-predef.h').
cp -a "$OUT/lib/gcc" "$OUT/usr/lib/gcc"
# GCC searches <prefix>/<configured-target>/bin (arm-linux-musleabihf) for
# the target assembler and binutils; without it the driver falls back to
# the host x86 'as' and rejects ARM -march flags.
mkdir -p "$OUT/usr/arm-linux-musleabihf"
cp -a "$OUT/arm-linux-musleabihf/bin" "$OUT/usr/arm-linux-musleabihf/bin"
mkdir -p "$OUT/usr/armv7-alpine-linux-musleabihf"
cp -a "$OUT/arm-linux-musleabihf/bin" "$OUT/usr/armv7-alpine-linux-musleabihf/"
cp -a "$OUT/arm-linux-musleabihf/include" "$OUT/usr/armv7-alpine-linux-musleabihf/"
for library in /lib/x86_64-linux-gnu/libgmp.so.10 /lib/x86_64-linux-gnu/libmpfr.so.6 /lib/x86_64-linux-gnu/libmpc.so.3 /usr/lib/x86_64-linux-gnu/libisl.so.23 /usr/lib/x86_64-linux-gnu/libz.so.1 /usr/lib/x86_64-linux-gnu/libzstd.so.1 /usr/lib/x86_64-linux-gnu/libjansson.so.4; do
  cp -- "$library" "$OUT/usr/lib/$(basename "$library")"
done
rm -f "$OUT/usr/lib/ld-musl-armhf.so.1"
cp -- "$OUT/arm-linux-musleabihf/lib/libc.so" "$OUT/usr/lib/ld-musl-armhf.so.1"
for tool in ar as c++ cc cpp elfedit gcc gcc-ar gcc-nm gcc-ranlib g++ ld ld.bfd nm objcopy objdump ranlib readelf size strings strip; do
  if [[ -f "$OUT/bin/arm-linux-musleabihf-$tool" ]]; then
    cp -- "$OUT/bin/arm-linux-musleabihf-$tool" "$OUT/usr/bin/armv7-alpine-linux-musleabihf-$tool"
  fi
done
printf 'toolchain_root=%s\n' "$OUT"
