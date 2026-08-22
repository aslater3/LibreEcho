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
rm -rf "$OUT/usr/bin" "$OUT/usr/include" "$OUT/usr/lib" "$OUT/usr/libexec" "$OUT/usr/armv7-alpine-linux-musleabihf"
mkdir -p "$OUT/usr/bin" "$OUT/usr/armv7-alpine-linux-musleabihf"
cp -a "$OUT/bin/." "$OUT/usr/bin/"
# GCC resolves cc1/cc1plus via ../libexec relative to the driver, so the
# usr/bin compiler copies need the libexec tree beside them.
cp -a "$OUT/libexec" "$OUT/usr/libexec"
cp -a "$OUT/arm-linux-musleabihf/include" "$OUT/usr/include"
cp -a "$OUT/arm-linux-musleabihf/lib" "$OUT/usr/lib"
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
