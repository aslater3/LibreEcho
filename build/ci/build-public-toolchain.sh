#!/usr/bin/env bash
# Build the ARM32 musl toolchain from public source into a job-local root.
set -euo pipefail
: "${RUNNER_TEMP:?set RUNNER_TEMP}"
OUT="${1:?output root}"
SRC="$RUNNER_TEMP/musl-cross-make-227df8b99103f9c59f6570babf892978e293082f"
rm -rf "$SRC" "$OUT"
git clone --quiet --depth 1 https://github.com/richfelker/musl-cross-make.git "$SRC"
git -C "$SRC" fetch --quiet --depth 1 origin 227df8b99103f9c59f6570babf892978e293082f
git -C "$SRC" checkout --quiet 227df8b99103f9c59f6570babf892978e293082f
printf '%s\n' 'TARGET = arm-linux-musleabihf' "OUTPUT = $OUT" > "$SRC/config.mak"
make -C "$SRC" -j"${JOBS:-2}"
mkdir -p "$OUT/usr"
ln -sfn ../bin "$OUT/usr/bin"
for tool in ar as c++ cc cpp elfedit gcc gcc-ar gcc-nm gcc-ranlib g++ ld ld.bfd nm objcopy objdump ranlib readelf size strings strip; do
  if [[ -e "$OUT/bin/arm-linux-musleabihf-$tool" ]]; then
    ln -sfn "arm-linux-musleabihf-$tool" "$OUT/bin/armv7-alpine-linux-musleabihf-$tool"
  fi
done
printf 'toolchain_root=%s\n' "$OUT"
