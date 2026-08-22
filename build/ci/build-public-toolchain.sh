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
printf '%s\n' 'TARGET = arm-linux-musleabihf' "OUTPUT = $OUT" 'COMMON_CONFIG += CFLAGS += -O2' > "$SRC/config.mak"
make -C "$SRC" -j"${JOBS:-2}"
printf 'toolchain_root=%s\n' "$OUT"
