#!/usr/bin/env bash
# Build one immutable, verified ARM32 recovery image.
#
# All active inputs and generated artifacts live below pipeline/.  A successful
# build publishes only a pointer in out/CURRENT; flash.sh refuses every image
# that is not reachable through that pointer and independently re-verifies it.
set -euo pipefail

PIPELINE="$(cd -- "$(dirname -- "$0")" && pwd -P)"
BUILD_ROOT_INPUT="${LIBREECHO_BUILD_ROOT:?ERROR: set LIBREECHO_BUILD_ROOT to an external build-state directory}"
mkdir -p "$BUILD_ROOT_INPUT"
BUILD_ROOT="$(cd -- "$BUILD_ROOT_INPUT" && pwd -P)"
WORK_ROOT="$BUILD_ROOT/work"
GENERATED_ROOT="$BUILD_ROOT/generated"
COMPONENT_TIMING_FILE="$GENERATED_ROOT/component-timing.log"
COMPONENT_IDENTITY_FILE="$GENERATED_ROOT/component-identities.log"
OUT="$BUILD_ROOT/out"
RUNS="$OUT/runs"
COMPONENT_CACHE_ROOT="${LIBREECHO_COMPONENT_CACHE_ROOT:-$BUILD_ROOT/component-cache}"
REUSE_COMPONENT_CACHE="${LIBREECHO_REUSE_COMPONENT_CACHE:-0}"
CACHE_TOOL="$PIPELINE/component-cache.py"
PRIVATE_ROOT="${LIBREECHO_PRIVATE_ROOT:?ERROR: set LIBREECHO_PRIVATE_ROOT outside the Git repository}"
mkdir -p "$WORK_ROOT" "$GENERATED_ROOT" "$RUNS" "$PRIVATE_ROOT"
[[ "$REUSE_COMPONENT_CACHE" == 0 || "$REUSE_COMPONENT_CACHE" == 1 ]] || {
  echo "ERROR: LIBREECHO_REUSE_COMPONENT_CACHE must be 0 or 1" >&2
  exit 1
}
[[ -f "$CACHE_TOOL" && ! -L "$CACHE_TOOL" ]] || {
  echo "ERROR: component cache tool is missing: $CACHE_TOOL" >&2
  exit 1
}
chmod 0700 "$PRIVATE_ROOT"
# Tooling packagers use this interface for generated work and the common
# payload packager.  The runtime root is external; only the reviewed helper is
# projected into it.
ln -sfn -- "$PIPELINE/package_feature_payload.sh" "$BUILD_ROOT/package_feature_payload.sh"
export LIBREECHO_PIPELINE_ROOT="$BUILD_ROOT"
IMAGE_PROFILE="${LIBREECHO_IMAGE_PROFILE:-development}"
if [[ -n "${LIBREECHO_SERVICE_PROFILE+x}" ]]; then
  SERVICE_PROFILE="$LIBREECHO_SERVICE_PROFILE"
elif [[ "$IMAGE_PROFILE" == ota ]]; then
  SERVICE_PROFILE=production
else
  SERVICE_PROFILE=diagnostic
fi
FEATURE_POLICY="${LIBREECHO_FEATURE_POLICY:-preserve}"
UPDATE_CHANNEL="${LIBREECHO_UPDATE_CHANNEL:-dev}"
KERNEL_SRC_INPUT="${LIBREECHO_KERNEL_SRC:?ERROR: set LIBREECHO_KERNEL_SRC explicitly}"
[[ -d "$KERNEL_SRC_INPUT" ]] || { echo "ERROR: kernel source directory not found: $KERNEL_SRC_INPUT" >&2; exit 1; }
KERNEL_SRC="$(cd -- "$KERNEL_SRC_INPUT" && pwd -P)"
TOOLING_SRC_INPUT="${LIBREECHO_TOOLING_SRC:?ERROR: set LIBREECHO_TOOLING_SRC explicitly}"
[[ -d "$TOOLING_SRC_INPUT" ]] || { echo "ERROR: tooling source directory not found: $TOOLING_SRC_INPUT" >&2; exit 1; }
TOOLING_SRC="$(cd -- "$TOOLING_SRC_INPUT" && pwd -P)"
TOOLS_DIR="$TOOLING_SRC/tools/mt8163-arm32"
KERNEL_OUT_INPUT="${LIBREECHO_KERNEL_OUT:?ERROR: set LIBREECHO_KERNEL_OUT explicitly outside source trees}"
mkdir -p "$KERNEL_OUT_INPUT"
KERNEL_OUT="$(cd -- "$KERNEL_OUT_INPUT" && pwd -P)"
INPUTS="${LIBREECHO_INPUTS_ROOT:-$PIPELINE/inputs}"
CONNECTIVITY_HELPERS="$GENERATED_ROOT/connectivity-helpers"
BUSYBOX_SOURCE_ARCHIVE="${LIBREECHO_BUSYBOX_SOURCE_ARCHIVE:?ERROR: set LIBREECHO_BUSYBOX_SOURCE_ARCHIVE explicitly}"
MUSL_SOURCE_ARCHIVE="${LIBREECHO_MUSL_SOURCE_ARCHIVE:?ERROR: set LIBREECHO_MUSL_SOURCE_ARCHIVE explicitly}"
WPA_SOURCE_ARCHIVE="${LIBREECHO_WPA_SUPPLICANT_SOURCE_ARCHIVE:?ERROR: set LIBREECHO_WPA_SUPPLICANT_SOURCE_ARCHIVE explicitly}"
WIRELESS_TOOLS_SOURCE_ARCHIVE="${LIBREECHO_WIRELESS_TOOLS_SOURCE_ARCHIVE:?ERROR: set LIBREECHO_WIRELESS_TOOLS_SOURCE_ARCHIVE explicitly}"
WIRELESS_REGDB_SOURCE_ARCHIVE="${LIBREECHO_WIRELESS_REGDB_SOURCE_ARCHIVE:?ERROR: set LIBREECHO_WIRELESS_REGDB_SOURCE_ARCHIVE explicitly}"
LIBSODIUM_SOURCE_ARCHIVE="${LIBREECHO_LIBSODIUM_SOURCE_ARCHIVE:?ERROR: set LIBREECHO_LIBSODIUM_SOURCE_ARCHIVE explicitly}"
MUSL_CROSS_PREFIX="${LIBREECHO_MUSL_CROSS_PREFIX:?ERROR: set LIBREECHO_MUSL_CROSS_PREFIX explicitly}"
BUSYBOX_OUTPUT="$GENERATED_ROOT/busybox-1.37.0"
MUSL_OUTPUT="$GENERATED_ROOT/musl-1.2.5"
MUSL_LOADER="$MUSL_OUTPUT/ld-musl-armhf.so.1"
WPA_OUTPUT="$GENERATED_ROOT/wpa_supplicant-2.10"
WPA_SUPPLICANT="$WPA_OUTPUT/wpa_supplicant"
WIFI_CONFIG="${WIFI_CONFIG:-$PRIVATE_ROOT/wpa_supplicant.conf}"
CROSS="${CROSS:-/usr/bin/arm-linux-gnueabihf-}"
AUDIO_CC="${AUDIO_CC:-${MUSL_CROSS_PREFIX}gcc}"
AUDIO_TOOLS_DIR="$GENERATED_ROOT/audio-tools"
WIRELESS_TOOLS_OUTPUT="$GENERATED_ROOT/wireless-tools"
WIRELESS_REGDB_OUTPUT="$GENERATED_ROOT/wireless-regdb"
LIBSODIUM_OUTPUT="$GENERATED_ROOT/libsodium-1.0.18"
NETWORK_TOOLS_BUILDER="$TOOLS_DIR/network-tools/build_wireless_tools.sh"
AIRPLAY_SYSROOT="${LIBREECHO_AIRPLAY_SYSROOT:?ERROR: set LIBREECHO_AIRPLAY_SYSROOT explicitly}"
if [[ -n "${LIBREECHO_AIRPLAY_CXX:-}" ]]; then
  AIRPLAY_CXX="$LIBREECHO_AIRPLAY_CXX"
elif [[ -x /usr/bin/arm-linux-gnueabihf-g++ ]]; then
  AIRPLAY_CXX=/usr/bin/arm-linux-gnueabihf-g++
else
  # The pinned AirPlay sources are C; use the target C driver when this host
  # lacks a separate ARMHF C++ driver.  A real g++ is preferred whenever it is
  # installed, and CI can pin LIBREECHO_AIRPLAY_CXX explicitly.
  AIRPLAY_CXX=/usr/bin/arm-linux-gnueabihf-gcc
fi
AIRPLAY_COMPILER_PATH="${LIBREECHO_AIRPLAY_COMPILER_PATH:-}"
AIRPLAY_HOST_BIN="$INPUTS/host-tools/bin"
AIRPLAY_HOST_LIB="$INPUTS/host-tools/lib"
AIRPLAY_PLISTUTIL="$AIRPLAY_HOST_BIN/plistutil"
AIRPLAY_ALSA_DATA="${LIBREECHO_AIRPLAY_ALSA_DATA:-/usr/share/alsa}"
AIRPLAY_TINYALSA_ARCHIVE="$INPUTS/tinyalsa-e43025bbf702eb7dd8edd48c1eb50530c60f1de8.tar.gz"
UI_SOURCE="${LIBREECHO_UI_SRC:?ERROR: set LIBREECHO_UI_SRC explicitly}"
UI_CROSS="${LIBREECHO_UI_CROSS:-/usr/bin/arm-linux-gnueabihf-}"
CORE_RUNTIME_SYSROOT="${LIBREECHO_CORE_RUNTIME_SYSROOT:?ERROR: set exact ARMHF glibc sysroot}"
CORE_GCC_LIBDIR="${LIBREECHO_CORE_GCC_LIBDIR:?ERROR: set exact ARMHF GCC runtime directory}"
SHERPA_SOURCE=
SHERPA_PREFIX=
ORT_BUILD=
ORT_PREFIX=
ESPEAK_SOURCE=
FLITE_SOURCE=
SPEEX_PREFIX=
SOURCE_OFFER_INPUTS=
ASSEMBLE_SOURCE_OFFERS="$PIPELINE/assemble-release-source-offers.sh"
TTS_NORTHERN_MALE_MODEL="${LIBREECHO_TTS_NORTHERN_MALE_MODEL:-}"
TTS_FEMALE_MODEL="${LIBREECHO_TTS_FEMALE_MODEL:-}"
TTS_TOKENS="${LIBREECHO_TTS_TOKENS:-}"
TTS_ESPEAK_DATA="${LIBREECHO_TTS_ESPEAK_DATA:-}"
WAKE_ORT_SOURCE="${LIBREECHO_WAKE_ORT_SOURCE:-}"
WAKE_FLATBUFFERS_PYTHON=
WAKE_ORT_BUILD="${LIBREECHO_WAKE_ORT_BUILD:-$GENERATED_ROOT/onnxruntime-wake-reduced}"
WAKE_SPEEX_PREFIX="${LIBREECHO_WAKE_SPEEX_PREFIX:-$GENERATED_ROOT/speexdsp-arm32}"
WAKE_SPEEX_ARCHIVE="$INPUTS/speexdsp-SpeexDSP-1.2.1.tar.gz"
WAKE_MEL_MODEL="$INPUTS/melspectrogram.onnx"
WAKE_EMBEDDING_MODEL="$INPUTS/embedding_model.onnx"
WAKE_CLASSIFIER_MODEL="$INPUTS/alexa_v0.1.onnx"
STT_MODEL_ROOT="${LIBREECHO_STT_MODEL_ROOT:-}"
STT_ENCODER="$STT_MODEL_ROOT/encoder-epoch-99-avg-1.int8.onnx"
STT_DECODER="$STT_MODEL_ROOT/decoder-epoch-99-avg-1.int8.onnx"
STT_JOINER="$STT_MODEL_ROOT/joiner-epoch-99-avg-1.int8.onnx"
STT_TOKENS="$STT_MODEL_ROOT/tokens.txt"
STT_MODEL_LICENSE="$STT_MODEL_ROOT/README.md"
ASSISTANT_CURL_SOURCE="$INPUTS/curl-8.21.0.tar.xz"
ASSISTANT_CA_BUNDLE="$INPUTS/ca-certificates-20260601.crt"
ASSISTANT_CA_COPYRIGHT="$INPUTS/ca-certificates-20260601.copyright"
SSH_ENABLED="${LIBREECHO_SSH_ENABLED:-0}"
SSH_ROOT_PASSWORD_HASH="${LIBREECHO_SSH_ROOT_PASSWORD_HASH:-}"
JOBS="${JOBS:-$(nproc)}"
OTA_DIR="$TOOLS_DIR/ota"
OTA_PUBLIC_KEY="$OTA_DIR/ota-public-key.hex"
OTA_SIGNING_KEY="${LIBREECHO_OTA_SIGNING_KEY:-$PRIVATE_ROOT/ota-signing-key.hex}"
OTA_SIGNING_MODE="${LIBREECHO_OTA_SIGNING_MODE:-github}"
OTA_SODIUM_ROOT="$LIBSODIUM_OUTPUT"
OTA_SODIUM_A="$OTA_SODIUM_ROOT/lib/libsodium.a"
OTA_MUSL_NATIVE_ROOT="${LIBREECHO_OTA_MUSL_NATIVE_ROOT:?ERROR: set LIBREECHO_OTA_MUSL_NATIVE_ROOT explicitly}"
OTA_MUSL_SYSROOT="${LIBREECHO_OTA_MUSL_SYSROOT:?ERROR: set LIBREECHO_OTA_MUSL_SYSROOT explicitly}"
OTA_MUSL_CC="$OTA_MUSL_NATIVE_ROOT/usr/bin/armv7-alpine-linux-musleabihf-gcc"
OTA_MUSL_GCC_LIBEXEC="$(
  cc1="$($OTA_MUSL_CC -print-prog-name=cc1)"
  cd -- "$(dirname -- "$cc1")" && pwd -P
)"
OTA_MUSL_TARGET_GCC_LIB="$(
  libgcc="$($OTA_MUSL_CC -print-libgcc-file-name)"
  cd -- "$(dirname -- "$libgcc")" && pwd -P
)"
ADBD_SOURCE="${LIBREECHO_ADBD_SOURCE:?ERROR: set LIBREECHO_ADBD_SOURCE to a clean pinned AOSP system/core checkout}"
ADBD_KERNEL_HEADERS="${LIBREECHO_ADBD_KERNEL_HEADERS:?ERROR: set LIBREECHO_ADBD_KERNEL_HEADERS to an exported Linux UAPI tree}"
ADBD_BUILDER="$TOOLS_DIR/adbd/build_adbd.sh"
BOOT_ENVELOPE_GENERATOR="$TOOLS_DIR/generate_boot_envelope.py"
KERNEL_ZIMAGE_OVERRIDE="${LIBREECHO_KERNEL_ZIMAGE_OVERRIDE:-}"
KERNEL_ZIMAGE_OVERRIDE_SHA256="${LIBREECHO_KERNEL_ZIMAGE_OVERRIDE_SHA256:-}"
KERNEL_SYSTEM_MAP_OVERRIDE="${LIBREECHO_KERNEL_SYSTEM_MAP_OVERRIDE:-}"
KERNEL_SYSTEM_MAP_OVERRIDE_SHA256="${LIBREECHO_KERNEL_SYSTEM_MAP_OVERRIDE_SHA256:-}"
AIRPLAY_PAYLOAD_OVERRIDE="${LIBREECHO_AIRPLAY_PAYLOAD_OVERRIDE:-}"
AIRPLAY_MANIFEST_OVERRIDE="${LIBREECHO_AIRPLAY_MANIFEST_OVERRIDE:-}"
ASSISTANT_PAYLOAD_OVERRIDE="${LIBREECHO_ASSISTANT_PAYLOAD_OVERRIDE:-}"
ASSISTANT_MANIFEST_OVERRIDE="${LIBREECHO_ASSISTANT_MANIFEST_OVERRIDE:-}"
PUBLIC_RELEASE_MODE="${LIBREECHO_PUBLIC_RELEASE:-0}"
PRODUCT_SRC="${LIBREECHO_PRODUCT_SRC:-}"
product_head=
product_state=not-applicable
product_diffsha=

source_state_sha256() {
  local repository=$1

  {
    git -C "$repository" diff --binary --full-index HEAD
    while IFS= read -r -d '' relative; do
      printf '\0untracked:%s\0' "$relative"
      sha256sum "$repository/$relative" | awk '{print $1}'
    done < <(
      git -C "$repository" ls-files --others --exclude-standard -z |
        LC_ALL=C sort -z
    )
  } | sha256sum | awk '{print $1}'
}

component_cache_key() {
  local component=$1
  shift
  python3 -B "$CACHE_TOOL" key --component "$component" "$@"
}

record_component_identity() {
  local identity=$1 digest=$2
  [[ "$identity" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ &&
     "$digest" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: invalid component identity record: $identity" >&2
    exit 1
  }
  printf 'identity=%s sha256=%s\n' "$identity" "$digest" \
    >>"$COMPONENT_IDENTITY_FILE"
}

component_cache_restore() {
  local component=$1 key=$2 destination=$3 rc
  COMPONENT_STARTED_MS["$component"]="$(date +%s%3N)"
  if [[ "$REUSE_COMPONENT_CACHE" != 1 ]]; then
    printf 'component_cache_miss=%s\n' "$component"
    return 1
  fi
  if python3 -B "$CACHE_TOOL" restore \
      --cache-root "$COMPONENT_CACHE_ROOT" --component "$component" \
      --key "$key" --destination "$destination"; then
    printf 'component_cache_hit=%s\n' "$component"
    return 0
  else
    rc=$?
    if [[ "$rc" == 3 ]]; then
      printf 'component_cache_miss=%s\n' "$component"
      return 1
    fi
    echo "ERROR: component cache verification failed for $component" >&2
    exit "$rc"
  fi
}

component_cache_store() {
  local component=$1 key=$2 source=$3
  python3 -B "$CACHE_TOOL" store \
    --cache-root "$COMPONENT_CACHE_ROOT" --component "$component" \
    --key "$key" --source "$source"
  printf 'component_cache_rebuilt=%s\n' "$component"
}

component_materialize() {
  local component=$1 key=$2 status=$3 source=$4 destination=$5 started finished
  python3 -B "$CACHE_TOOL" materialize \
    --component "$component" --key "$key" --status "$status" \
    --source "$source" --destination "$destination" --manifest "$COMPONENTS_MANIFEST"
  finished="$(date +%s%3N)"
  started="${COMPONENT_STARTED_MS[$component]:-$finished}"
  printf 'component_timing component=%s status=%s duration_ms=%s\n' \
    "$component" "$status" "$((finished-started))" >>"$COMPONENT_TIMING_FILE"
}

require_relink_tree() {
  local root=$1 label=$2
  [[ -d "$root" && ! -L "$root" ]] || {
    echo "ERROR: $label relink tree is missing or unsafe: $root" >&2
    exit 1
  }
  [[ -z "$(find "$root" -type l -print -quit)" ]] || {
    echo "ERROR: $label relink tree contains a symlink: $root" >&2
    exit 1
  }
  [[ -n "$(find "$root" -type f \( -name '*.o' -o -name '*.a' \) -print -quit)" ]] || {
    echo "ERROR: $label relink tree contains no object/archive files: $root" >&2
    exit 1
  }
}

verify_pinned_input() {
  local relative=$1 manifest="$INPUTS/SHA256SUMS" expected actual
  [[ "$relative" != /* && "$relative" != *..* && -f "$INPUTS/$relative" &&
     ! -L "$INPUTS/$relative" && -f "$manifest" && ! -L "$manifest" ]] || {
    echo "ERROR: unsafe or missing pinned input: $relative" >&2; return 1
  }
  expected="$(python3 - "$manifest" "$relative" <<'PY'
import pathlib, sys
manifest, wanted = pathlib.Path(sys.argv[1]), sys.argv[2]
matches = []
for line in manifest.read_text().splitlines():
    if "  " not in line:
        continue
    digest, name = line.split("  ", 1)
    if name == wanted:
        matches.append(digest)
if len(matches) != 1:
    raise SystemExit(1)
print(matches[0])
PY
)" || { echo "ERROR: input has no unique SHA256SUMS record: $relative" >&2; return 1; }
  actual="$(sha256sum "$INPUTS/$relative" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || {
    echo "ERROR: pinned input hash mismatch: $relative" >&2; return 1
  }
}

adopt_feature_payload() {
  local feature=$1 source_payload=$2 source_manifest=$3 output_payload=$4 output_manifest=$5
  [[ -n "$source_payload" && -n "$source_manifest" ]] || {
    echo "ERROR: parity override for $feature requires payload and manifest" >&2; exit 1
  }
  [[ -f "$source_payload" && ! -L "$source_payload" &&
     -f "$source_manifest" && ! -L "$source_manifest" ]] || {
    echo "ERROR: parity override for $feature is not a regular payload/manifest pair" >&2; exit 1
  }
  cp -- "$source_payload" "$output_payload"
  cp -- "$source_manifest" "$output_manifest"
}

usage() {
  cat <<'EOF'
Usage: ./build.sh [--defconfig] [--profile development|ota] \
  [--service-profile diagnostic|production] \
  [--feature-policy exclude|preserve|redistributable|community-noncommercial] \
  [--update-channel dev|stable] \
  [--reuse-components] \
  [--no-publish]

Builds the current LibreEcho-Kernel ARM32 zImage incrementally, generates the
reviewed Radar Puffin Android-v0/MediaTek envelope, packages it with the audited
recovery service, runs the independent image verifier, and publishes
pipeline/out/CURRENT.

--no-publish writes the immutable run and its CURRENT.candidate record but
leaves canonical pipeline/out/CURRENT and pipeline/out/current untouched.

No image is overwritten. Every run gets its own immutable directory.
EOF
}

defconfig=0
publish_current=1
while (($#)); do
  case "$1" in
    --defconfig) defconfig=1 ;;
    --no-publish) publish_current=0 ;;
    --profile)
      shift
      (($#)) || { echo "ERROR: --profile requires a value" >&2; exit 1; }
      IMAGE_PROFILE=$1
      ;;
    --service-profile)
      shift
      (($#)) || { echo "ERROR: --service-profile requires a value" >&2; exit 1; }
      SERVICE_PROFILE=$1
      ;;
    --feature-policy)
      shift
      (($#)) || { echo "ERROR: --feature-policy requires a value" >&2; exit 1; }
      FEATURE_POLICY=$1
      ;;
    --update-channel)
      shift
      (($#)) || { echo "ERROR: --update-channel requires a value" >&2; exit 1; }
      UPDATE_CHANNEL=$1
      ;;
    --reuse-components)
      REUSE_COMPONENT_CACHE=1
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done
case "$IMAGE_PROFILE" in development|ota) ;; *) echo "ERROR: invalid image profile: $IMAGE_PROFILE" >&2; exit 1 ;; esac
case "$SERVICE_PROFILE" in diagnostic|production) ;; *) echo "ERROR: invalid service profile: $SERVICE_PROFILE" >&2; exit 1 ;; esac
case "$UPDATE_CHANNEL" in dev|stable) ;; *) echo "ERROR: invalid update channel: $UPDATE_CHANNEL" >&2; exit 1 ;; esac
FEATURES_ENABLED=0
WAKEWORD_ENABLED=0
policy_token=
release_scope=
case "$FEATURE_POLICY" in
  exclude) policy_token=exclude ;;
  preserve) FEATURES_ENABLED=1; WAKEWORD_ENABLED=1; policy_token=preserve ;;
  redistributable) policy_token=redistrib; FEATURES_ENABLED=1; WAKEWORD_ENABLED=0; release_scope=commercially-unrestricted ;;
  community-noncommercial) policy_token=community-nc; FEATURES_ENABLED=1; WAKEWORD_ENABLED=1; release_scope=community-noncommercial ;;
  *) echo "ERROR: invalid feature policy: $FEATURE_POLICY" >&2; exit 1 ;;
esac
case "$PUBLIC_RELEASE_MODE" in 0|1) ;; *) echo "ERROR: LIBREECHO_PUBLIC_RELEASE must be 0 or 1" >&2; exit 1 ;; esac
if [[ "$FEATURE_POLICY" == exclude && "$SERVICE_PROFILE" != diagnostic ]]; then
  echo "ERROR: feature exclusion requires --service-profile diagnostic" >&2
  exit 1
fi
if [[ "$PUBLIC_RELEASE_MODE" == 1 ]]; then
  [[ $publish_current -eq 0 ]] || {
    echo "ERROR: public-release mode requires --no-publish" >&2; exit 1
  }
  [[ -n "$PRODUCT_SRC" ]] || {
    echo "ERROR: set LIBREECHO_PRODUCT_SRC to an explicit product Git worktree" >&2; exit 1
  }
  [[ -n "$release_scope" ]] || {
    echo "ERROR: public-release mode requires redistributable or community-noncommercial policy" >&2
    exit 1
  }
  git -C "$PRODUCT_SRC" rev-parse --show-toplevel >/dev/null 2>&1 || {
    echo "ERROR: product source is not a Git worktree: $PRODUCT_SRC" >&2; exit 1
  }
  [[ -z "$(git -C "$PRODUCT_SRC" status --porcelain=v1)" ]] || {
    echo "ERROR: public product source worktree is dirty" >&2; exit 1
  }
  [[ -x "$PRODUCT_SRC/tools/check-release-components.py" ]] || {
    echo "ERROR: public product component gate is unavailable" >&2; exit 1
  }
  python3 -B "$PRODUCT_SRC/tools/check-release-components.py" \
    --components "$PRODUCT_SRC/release/components.json" \
    --release-scope "$release_scope"
  product_head="$(git -C "$PRODUCT_SRC" rev-parse HEAD)"
  product_state=clean
  product_diffsha="$(source_state_sha256 "$PRODUCT_SRC")"
fi
if [[ "$FEATURES_ENABLED" == 1 && "$SERVICE_PROFILE" == production ]]; then
  fpga_firmware="$KERNEL_SRC/firmware/i2s_to_spi_v34.bin"
  [[ -f "$fpga_firmware" && ! -L "$fpga_firmware" ]] || {
    echo "ERROR: full-feature production build requires the MT8163 audio FPGA firmware" >&2; exit 1
  }
  [[ "$(sha256sum "$fpga_firmware" | awk '{print $1}')" == \
     77a558bacdaaf9e343f02f2d74f27a5f2bb2dc8b6d66cc2499b60ed14ef62fe6 ]] || {
    echo "ERROR: MT8163 audio FPGA firmware identity mismatch" >&2; exit 1
  }
  grep -qx 'CONFIG_SND_SOC_AMZN_MT8163_SPI_AUDIO=y' \
    "$KERNEL_SRC/arch/arm/configs/mt8163_arm32_defconfig" || {
    echo "ERROR: full-feature production build requires the verified MT8163 audio driver" >&2; exit 1
  }
fi
case "$OTA_SIGNING_MODE" in github|local) ;; *) echo "ERROR: invalid OTA signing mode: $OTA_SIGNING_MODE" >&2; exit 1 ;; esac

[[ -x "${CROSS}gcc" ]] || { echo "ERROR: ARM32 compiler not found: ${CROSS}gcc" >&2; exit 1; }
[[ -x "$AUDIO_CC" ]] || { echo "ERROR: ARM32 audio probe compiler not found: $AUDIO_CC" >&2; exit 1; }
git -C "$KERNEL_SRC" rev-parse --show-toplevel >/dev/null 2>&1 || {
  echo "ERROR: kernel source is not a Git worktree: $KERNEL_SRC" >&2; exit 1;
}
git -C "$TOOLING_SRC" rev-parse --show-toplevel >/dev/null 2>&1 || {
  echo "ERROR: tooling source is not a Git worktree: $TOOLING_SRC" >&2; exit 1;
}
[[ -d "$TOOLS_DIR" ]] || {
  echo "ERROR: MT8163 tooling directory not found: $TOOLS_DIR" >&2; exit 1;
}
[[ -d "$UI_SOURCE" ]] || { echo "ERROR: missing LibreEcho-UI source: $UI_SOURCE" >&2; exit 1; }
if [[ "$FEATURES_ENABLED" == 1 ]]; then
  for tts_input in "$TTS_NORTHERN_MALE_MODEL" "$TTS_FEMALE_MODEL" "$TTS_TOKENS"; do
    [[ -f "$tts_input" && ! -L "$tts_input" ]] || {
      echo "ERROR: missing reviewed TTS input: $tts_input" >&2
      exit 1
    }
  done
  [[ -d "$TTS_ESPEAK_DATA" && ! -L "$TTS_ESPEAK_DATA" ]] || {
    echo "ERROR: missing reviewed eSpeak runtime data: $TTS_ESPEAK_DATA" >&2
    exit 1
  }
  if [[ "$WAKEWORD_ENABLED" == 1 ]]; then
    for wake_input in "$WAKE_SPEEX_ARCHIVE" "$WAKE_MEL_MODEL" \
        "$WAKE_EMBEDDING_MODEL" "$WAKE_CLASSIFIER_MODEL"; do
      [[ -f "$wake_input" && ! -L "$wake_input" ]] || {
        echo "ERROR: missing reviewed wakeword input: $wake_input" >&2
        exit 1
      }
    done
    [[ -d "$WAKE_ORT_SOURCE/.git" ]] || {
      echo "ERROR: missing pinned ONNX Runtime source: $WAKE_ORT_SOURCE" >&2
      exit 1
    }
  fi
  for stt_input in "$STT_ENCODER" "$STT_DECODER" "$STT_JOINER" \
      "$STT_TOKENS" "$STT_MODEL_LICENSE"; do
    [[ -f "$stt_input" && ! -L "$stt_input" ]] || {
      echo "ERROR: missing reviewed English STT input: $stt_input" >&2
      exit 1
    }
  done
  for assistant_input in "$ASSISTANT_CURL_SOURCE" "$ASSISTANT_CA_BUNDLE" \
      "$ASSISTANT_CA_COPYRIGHT"; do
    [[ -f "$assistant_input" && ! -L "$assistant_input" ]] || {
      echo "ERROR: missing reviewed assistant runtime input: $assistant_input" >&2
      exit 1
    }
  done
  [[ -f "$AIRPLAY_ALSA_DATA/alsa.conf" ]] || {
    echo "ERROR: missing host ALSA runtime data: $AIRPLAY_ALSA_DATA" >&2
    echo "       Install the architecture-independent libasound2-data package or set LIBREECHO_AIRPLAY_ALSA_DATA." >&2
    exit 1
  }
  [[ -x "$AIRPLAY_PLISTUTIL" && ! -L "$AIRPLAY_PLISTUTIL" ]] || {
    echo "ERROR: missing regular pinned host plistutil: $AIRPLAY_PLISTUTIL" >&2
    exit 1
  }
  [[ -d "$AIRPLAY_HOST_LIB" && ! -L "$AIRPLAY_HOST_LIB" &&
     -f "$AIRPLAY_HOST_LIB/libplist-2.0.so.4" &&
     ! -L "$AIRPLAY_HOST_LIB/libplist-2.0.so.4" ]] || {
    echo "ERROR: missing regular pinned host plistutil runtime: $AIRPLAY_HOST_LIB/libplist-2.0.so.4" >&2
    exit 1
  }
  [[ "$(find "$AIRPLAY_HOST_BIN" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)" == plistutil ]] || {
    echo "ERROR: pinned host plistutil directory contains unexpected entries" >&2
    exit 1
  }
  [[ "$(find "$AIRPLAY_HOST_LIB" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)" == libplist-2.0.so.4 ]] || {
    echo "ERROR: pinned host plistutil runtime directory contains unexpected entries" >&2
    exit 1
  }
  [[ -f "$AIRPLAY_TINYALSA_ARCHIVE" ]] || {
    echo "ERROR: missing pinned TinyALSA source archive: $AIRPLAY_TINYALSA_ARCHIVE" >&2
    exit 1
  }
fi
[[ -x "${UI_CROSS}gcc" ]] || {
  echo "ERROR: LibreEcho-UI ARM32 compiler not found: ${UI_CROSS}gcc" >&2; exit 1;
}
git -C "$UI_SOURCE" rev-parse --show-toplevel >/dev/null 2>&1 || {
  echo "ERROR: LibreEcho-UI source is not a Git worktree: $UI_SOURCE" >&2; exit 1;
}
[[ -f "$BOOT_ENVELOPE_GENERATOR" ]] || { echo "ERROR: boot-envelope generator is missing" >&2; exit 1; }
[[ -f "$OTA_PUBLIC_KEY" && ! -L "$OTA_PUBLIC_KEY" ]] || { echo "ERROR: missing OTA public key" >&2; exit 1; }
if [[ "$IMAGE_PROFILE" == ota && "$OTA_SIGNING_MODE" == local ]]; then
  [[ -f "$OTA_SIGNING_KEY" && ! -L "$OTA_SIGNING_KEY" ]] || {
    echo "ERROR: OTA profile requires a local signing key: $OTA_SIGNING_KEY" >&2
    exit 1
  }
  signing_mode="$(stat -c %a "$OTA_SIGNING_KEY")"
  (( (8#$signing_mode & 8#077) == 0 )) || {
    echo "ERROR: OTA signing key must not be group/world accessible" >&2
    exit 1
  }
fi
[[ -x "$OTA_MUSL_CC" && -f "$OTA_MUSL_SYSROOT/usr/include/errno.h" ]] || {
  echo "ERROR: missing ARMv7 musl compiler/sysroot for OTA boot control" >&2
  exit 1
}
for source_archive in "$BUSYBOX_SOURCE_ARCHIVE" "$MUSL_SOURCE_ARCHIVE" "$WPA_SOURCE_ARCHIVE" "$WIRELESS_TOOLS_SOURCE_ARCHIVE" "$WIRELESS_REGDB_SOURCE_ARCHIVE" "$LIBSODIUM_SOURCE_ARCHIVE"; do
  [[ -f "$source_archive" && ! -L "$source_archive" ]] || {
    echo "ERROR: missing public source archive: $source_archive" >&2
    exit 1
  }
done
[[ -x "${MUSL_CROSS_PREFIX}gcc" ]] || {
  echo "ERROR: musl ARMv7 cross compiler is unavailable: ${MUSL_CROSS_PREFIX}gcc" >&2
  exit 1
}
case "$SSH_ENABLED" in
  0) [[ -z "$SSH_ROOT_PASSWORD_HASH" ]] || {
       echo "ERROR: LIBREECHO_SSH_ROOT_PASSWORD_HASH requires LIBREECHO_SSH_ENABLED=1" >&2
       exit 1
     } ;;
  1) [[ -n "$SSH_ROOT_PASSWORD_HASH" && -f "$SSH_ROOT_PASSWORD_HASH" && ! -L "$SSH_ROOT_PASSWORD_HASH" ]] || {
       echo "ERROR: SSH requires a regular build-local root password hash file" >&2
       exit 1
     }
     hash_mode="$(stat -c %a "$SSH_ROOT_PASSWORD_HASH")"
     if (( 8#$hash_mode & 022 )); then
       echo "ERROR: SSH root password hash file is group/world-writable" >&2
       exit 1
     fi ;;
  *) echo "ERROR: LIBREECHO_SSH_ENABLED must be 0 or 1" >&2; exit 1 ;;
esac
[[ -x "$TOOLS_DIR/busybox/build_busybox.sh" && -x "$TOOLS_DIR/musl/build_musl.sh" && \
   -x "$TOOLS_DIR/wpa-supplicant/build_wpa_supplicant.sh" && \
   -x "$TOOLS_DIR/connectivity/build_connectivity_helpers.sh" && \
   -x "$TOOLS_DIR/audio-tools/build_audio_tools.sh" ]] || {
  echo "ERROR: public core source builders are unavailable" >&2; exit 1;
}
if [[ "$PUBLIC_RELEASE_MODE" != 1 ]]; then
[[ -f "$WIFI_CONFIG" && ! -L "$WIFI_CONFIG" ]] || {
  echo "ERROR: missing build-local Wi-Fi profile: $WIFI_CONFIG" >&2
  echo "       Copy initramfs/wpa_supplicant.conf.example and configure it privately." >&2
  exit 1
}
fi
IMAGE_WIFI_CONFIG="$WIFI_CONFIG"
if [[ "$PUBLIC_RELEASE_MODE" == 1 ]]; then
  IMAGE_WIFI_CONFIG="$GENERATED_ROOT/public-wpa_supplicant.conf"
  "$PIPELINE/prepare-public-wifi-config.sh" "$IMAGE_WIFI_CONFIG"
fi
pinned_inputs=(tinyalsa-e43025bbf702eb7dd8edd48c1eb50530c60f1de8.tar.gz)
if [[ "$FEATURES_ENABLED" == 1 ]]; then
  SHERPA_SOURCE="${LIBREECHO_SHERPA_SOURCE:?ERROR: set LIBREECHO_SHERPA_SOURCE explicitly}"
  SHERPA_PREFIX="${LIBREECHO_SHERPA_PREFIX:?ERROR: set LIBREECHO_SHERPA_PREFIX explicitly}"
  ORT_BUILD="${LIBREECHO_ORT_BUILD:?ERROR: set LIBREECHO_ORT_BUILD explicitly}"
  ORT_PREFIX="${LIBREECHO_ORT_PREFIX:?ERROR: set LIBREECHO_ORT_PREFIX explicitly}"
  ESPEAK_SOURCE="${LIBREECHO_ESPEAK_SOURCE:?ERROR: set LIBREECHO_ESPEAK_SOURCE explicitly}"
  FLITE_SOURCE="${LIBREECHO_FLITE_SOURCE:?ERROR: set LIBREECHO_FLITE_SOURCE explicitly}"
  SPEEX_PREFIX="${LIBREECHO_SPEEX_PREFIX:?ERROR: set LIBREECHO_SPEEX_PREFIX explicitly}"
  if [[ "$PUBLIC_RELEASE_MODE" != 1 ]]; then
    SOURCE_OFFER_INPUTS="${LIBREECHO_SOURCE_OFFER_INPUTS:?ERROR: set LIBREECHO_SOURCE_OFFER_INPUTS explicitly}"
  fi
  feature_directories=("$SHERPA_SOURCE" "$SHERPA_PREFIX" "$ORT_BUILD" "$ORT_PREFIX" "$ESPEAK_SOURCE" "$FLITE_SOURCE" "$SPEEX_PREFIX")
  [[ "$PUBLIC_RELEASE_MODE" == 1 ]] || feature_directories+=("$SOURCE_OFFER_INPUTS")
  for directory in "${feature_directories[@]}"; do
    [[ -d "$directory" && ! -L "$directory" ]] || {
      echo "ERROR: explicit feature dependency is missing or a symlink: $directory" >&2
      exit 1
    }
  done
  if [[ "$PUBLIC_RELEASE_MODE" != 1 ]]; then
  [[ -x "$ASSEMBLE_SOURCE_OFFERS" ]] || {
    echo "ERROR: source-offer assembler is unavailable: $ASSEMBLE_SOURCE_OFFERS" >&2
    exit 1
  }
  fi
  pinned_inputs+=(
    curl-8.21.0.tar.xz ca-certificates-20260601.crt
    ca-certificates-20260601.copyright nqptp-1.2.8.tar.gz
    shairport-sync-5.1.tar.gz ffmpeg-6.1.1.tar.xz
    host-tools/bin/plistutil host-tools/lib/libplist-2.0.so.4
    host-tools/manifest.json host-tools/share/libplist/copyright
    host-tools/share/libplist/COPYING.LESSER
    host-tools/source/libplist_2.3.0.orig.tar.bz2
    host-tools/source/libplist_2.3.0-1~exp2build2.debian.tar.xz
    host-tools/source/libplist_2.3.0-1~exp2build2.dsc
  )
  if [[ "$WAKEWORD_ENABLED" == 1 ]]; then
    WAKE_FLATBUFFERS_PYTHON="${LIBREECHO_WAKE_FLATBUFFERS_PYTHON:?ERROR: set LIBREECHO_WAKE_FLATBUFFERS_PYTHON explicitly}"
    [[ -d "$WAKE_FLATBUFFERS_PYTHON" && ! -L "$WAKE_FLATBUFFERS_PYTHON" ]] || {
      echo "ERROR: explicit wakeword dependency is missing or a symlink: $WAKE_FLATBUFFERS_PYTHON" >&2
      exit 1
    }
    pinned_inputs+=(speexdsp-SpeexDSP-1.2.1.tar.gz melspectrogram.onnx embedding_model.onnx alexa_v0.1.onnx)
  fi
fi
for pinned_input in "${pinned_inputs[@]}"; do
  verify_pinned_input "$pinned_input"
done
if [[ "$FEATURES_ENABLED" == 1 ]]; then
  env LD_LIBRARY_PATH="$AIRPLAY_HOST_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$AIRPLAY_PLISTUTIL" --help >/dev/null 2>&1 || {
      echo "ERROR: pinned host plistutil runtime check failed" >&2
      exit 1
    }
fi

mkdir -p "$KERNEL_OUT" "$RUNS"
exec 9>"$PIPELINE/.build.lock"
flock -n 9 || { echo "ERROR: another build is already running" >&2; exit 1; }
: >"$COMPONENT_TIMING_FILE"
: >"$COMPONENT_IDENTITY_FILE"
declare -A COMPONENT_STARTED_MS=()

# Cache keys are content-addressed and component-specific.  The clean lane
# (default) still rebuilds every component, but stores the verified outputs so
# an explicit iteration build can reuse only unchanged components.
ota_musl_key_args=(
  --tree "ota-musl-gcc-libexec=$OTA_MUSL_GCC_LIBEXEC"
  --file "ota-musl-native-isl=$OTA_MUSL_NATIVE_ROOT/usr/lib/libisl.so.23"
  --file "ota-musl-native-mpc=$OTA_MUSL_NATIVE_ROOT/usr/lib/libmpc.so.3"
  --file "ota-musl-native-mpfr=$OTA_MUSL_NATIVE_ROOT/usr/lib/libmpfr.so.6"
  --file "ota-musl-native-gmp=$OTA_MUSL_NATIVE_ROOT/usr/lib/libgmp.so.10"
  --file "ota-musl-native-zlib=$OTA_MUSL_NATIVE_ROOT/usr/lib/libz.so.1"
  --file "ota-musl-native-zstd=$OTA_MUSL_NATIVE_ROOT/usr/lib/libzstd.so.1"
  --file "ota-musl-native-jansson=$OTA_MUSL_NATIVE_ROOT/usr/lib/libjansson.so.4"
  --tree "ota-musl-target-bin=$OTA_MUSL_NATIVE_ROOT/usr/armv7-alpine-linux-musleabihf/bin"
  --tree "ota-musl-target-include=$OTA_MUSL_NATIVE_ROOT/usr/armv7-alpine-linux-musleabihf/include"
  --tree "ota-musl-target-gcc-lib=$OTA_MUSL_TARGET_GCC_LIB"
  --tree "ota-musl-sysroot-include=$OTA_MUSL_SYSROOT/usr/include"
)
for link_input in \
    crt1.o Scrt1.o rcrt1.o crti.o crtn.o libc.a libpthread.a libdl.a \
    librt.a libm.a libresolv.a libutil.a libcrypt.a libxnet.a \
    libssp_nonshared.a libstdc++.a libsupc++.a libatomic.a \
    ld-musl-armhf.so.1 libc.so; do
  link_label=${link_input//[^A-Za-z0-9_.-]/_}
  ota_musl_key_args+=(
    --file "ota-musl-sysroot-$link_label=$OTA_MUSL_SYSROOT/usr/lib/$link_input"
  )
done
CORE_TOOLCHAIN_KEY="$(component_cache_key toolchain \
  --value "toolchain_target=armv7-musleabihf" \
  --value "ota_toolchain_contract=v1" \
  --file "musl-gcc=${MUSL_CROSS_PREFIX}gcc" \
  --file "musl-ar=${MUSL_CROSS_PREFIX}ar" \
  --file "musl-ranlib=${MUSL_CROSS_PREFIX}ranlib" \
  --file "musl-strip=${MUSL_CROSS_PREFIX}strip" \
  --file "ota-musl-gcc=$OTA_MUSL_CC" \
  --file "ota-musl-ar=$OTA_MUSL_NATIVE_ROOT/usr/bin/armv7-alpine-linux-musleabihf-ar" \
  --file "ota-musl-ranlib=$OTA_MUSL_NATIVE_ROOT/usr/bin/armv7-alpine-linux-musleabihf-ranlib" \
  --file "ota-musl-strip=$OTA_MUSL_NATIVE_ROOT/usr/bin/armv7-alpine-linux-musleabihf-strip" \
  "${ota_musl_key_args[@]}")"
printf 'identity=core-toolchain sha256=%s\n' "$CORE_TOOLCHAIN_KEY" >>"$COMPONENT_IDENTITY_FILE"
echo "component_cache_root=$COMPONENT_CACHE_ROOT"
echo "component_cache_reuse=$REUSE_COMPONENT_CACHE"

echo "=== rebuilding or restoring core runtime components ==="
for component_dir in "$BUSYBOX_OUTPUT" "$MUSL_OUTPUT" "$WPA_OUTPUT" \
    "$CONNECTIVITY_HELPERS" "$AUDIO_TOOLS_DIR" "$WIRELESS_TOOLS_OUTPUT" \
    "$WIRELESS_REGDB_OUTPUT" "$LIBSODIUM_OUTPUT"; do
  rm -rf "$component_dir"
done

musl_cache_key="$(component_cache_key musl \
  --tree "platform-musl=$TOOLS_DIR/musl" --value "toolchain=$CORE_TOOLCHAIN_KEY" \
  --file "source-archive=$MUSL_SOURCE_ARCHIVE" --file "builder=$TOOLS_DIR/musl/build_musl.sh")"
musl_status=rebuilt
if ! component_cache_restore musl "$musl_cache_key" "$MUSL_OUTPUT"; then
  "$TOOLS_DIR/musl/build_musl.sh" \
    --archive "$MUSL_SOURCE_ARCHIVE" --output "$MUSL_OUTPUT" \
    --cc "${MUSL_CROSS_PREFIX}gcc" | tee "$GENERATED_ROOT/musl-build.log"
  component_cache_store musl "$musl_cache_key" "$MUSL_OUTPUT"
else
  musl_status=hit
  printf 'component_cache_hit=musl\n' >"$GENERATED_ROOT/musl-build.log"
fi

busybox_cache_key="$(component_cache_key busybox \
  --tree "platform-busybox=$TOOLS_DIR/busybox" --value "toolchain=$CORE_TOOLCHAIN_KEY" \
  --file "source-archive=$BUSYBOX_SOURCE_ARCHIVE" --file "builder=$TOOLS_DIR/busybox/build_busybox.sh")"
busybox_status=rebuilt
if ! component_cache_restore busybox "$busybox_cache_key" "$BUSYBOX_OUTPUT"; then
  "$TOOLS_DIR/busybox/build_busybox.sh" \
    --archive "$BUSYBOX_SOURCE_ARCHIVE" --output "$BUSYBOX_OUTPUT" \
    --cross-prefix "$MUSL_CROSS_PREFIX" --sysroot "$OTA_MUSL_SYSROOT" \
    | tee "$GENERATED_ROOT/busybox-build.log"
  component_cache_store busybox "$busybox_cache_key" "$BUSYBOX_OUTPUT"
else
  busybox_status=hit
  printf 'component_cache_hit=busybox\n' >"$GENERATED_ROOT/busybox-build.log"
fi

wpa_cache_key="$(component_cache_key wpa-supplicant \
  --tree "platform-wpa=$TOOLS_DIR/wpa-supplicant" --value "toolchain=$CORE_TOOLCHAIN_KEY" \
  --tree "linux-uapi=$ADBD_KERNEL_HEADERS" --file "source-archive=$WPA_SOURCE_ARCHIVE" \
  --file "builder=$TOOLS_DIR/wpa-supplicant/build_wpa_supplicant.sh")"
wpa_status=rebuilt
if ! component_cache_restore wpa-supplicant "$wpa_cache_key" "$WPA_OUTPUT"; then
  "$TOOLS_DIR/wpa-supplicant/build_wpa_supplicant.sh" \
    --archive "$WPA_SOURCE_ARCHIVE" --output "$WPA_OUTPUT" \
    --cc "${MUSL_CROSS_PREFIX}gcc" --sysroot "$OTA_MUSL_SYSROOT" \
    --kernel-headers "$ADBD_KERNEL_HEADERS" \
    | tee "$GENERATED_ROOT/wpa-supplicant-build.log"
  component_cache_store wpa-supplicant "$wpa_cache_key" "$WPA_OUTPUT"
else
  wpa_status=hit
  printf 'component_cache_hit=wpa-supplicant\n' >"$GENERATED_ROOT/wpa-supplicant-build.log"
fi

connectivity_cache_key="$(component_cache_key connectivity \
  --tree "platform-connectivity=$TOOLS_DIR/connectivity" --value "toolchain=$CORE_TOOLCHAIN_KEY" \
  --file "builder=$TOOLS_DIR/connectivity/build_connectivity_helpers.sh")"
connectivity_status=rebuilt
if ! component_cache_restore connectivity "$connectivity_cache_key" "$CONNECTIVITY_HELPERS"; then
  "$TOOLS_DIR/connectivity/build_connectivity_helpers.sh" \
    --output "$CONNECTIVITY_HELPERS" --cc "${MUSL_CROSS_PREFIX}gcc" \
    --sysroot "$OTA_MUSL_SYSROOT" \
    | tee "$GENERATED_ROOT/connectivity-build.log"
  component_cache_store connectivity "$connectivity_cache_key" "$CONNECTIVITY_HELPERS"
else
  connectivity_status=hit
  printf 'component_cache_hit=connectivity\n' >"$GENERATED_ROOT/connectivity-build.log"
fi

audio_tools_cache_key="$(component_cache_key audio-tools \
  --tree "platform-audio-tools=$TOOLS_DIR/audio-tools" --value "toolchain=$CORE_TOOLCHAIN_KEY" \
  --tree "linux-uapi=$ADBD_KERNEL_HEADERS" --file "tinyalsa-archive=$AIRPLAY_TINYALSA_ARCHIVE" \
  --file "builder=$TOOLS_DIR/audio-tools/build_audio_tools.sh")"
audio_tools_status=rebuilt
if ! component_cache_restore audio-tools "$audio_tools_cache_key" "$AUDIO_TOOLS_DIR"; then
  "$TOOLS_DIR/audio-tools/build_audio_tools.sh" \
    --archive "$AIRPLAY_TINYALSA_ARCHIVE" --output "$AUDIO_TOOLS_DIR" \
    --cross-prefix "$MUSL_CROSS_PREFIX" --sysroot "$OTA_MUSL_SYSROOT" \
    --kernel-headers "$ADBD_KERNEL_HEADERS" \
    | tee "$GENERATED_ROOT/audio-tools-build.log"
  component_cache_store audio-tools "$audio_tools_cache_key" "$AUDIO_TOOLS_DIR"
else
  audio_tools_status=hit
  printf 'component_cache_hit=audio-tools\n' >"$GENERATED_ROOT/audio-tools-build.log"
fi
busybox_sha="$(sha256sum "$BUSYBOX_OUTPUT/busybox" | awk '{print $1}')"
musl_loader_sha="$(sha256sum "$MUSL_LOADER" | awk '{print $1}')"
wpa_supplicant_sha="$(sha256sum "$WPA_SUPPLICANT" | awk '{print $1}')"
echo "busybox_sha256=$busybox_sha"
echo "musl_loader_sha256=$musl_loader_sha"
echo "wpa_supplicant_sha256=$wpa_supplicant_sha"

if ((defconfig)) || [[ ! -f "$KERNEL_OUT/.config" ]]; then
  if ((defconfig)); then
    echo "=== regenerating ARM32 defconfig (requested) ==="
  else
    echo "=== generating ARM32 defconfig (new build directory) ==="
  fi
  make -C "$KERNEL_SRC" O="$KERNEL_OUT" ARCH=arm CROSS_COMPILE="$CROSS" \
    LD="${CROSS}ld.bfd" mt8163_arm32_defconfig
fi

recovery_marker_kconfig=0
if grep -Rqs --include='Kconfig*' 'config LIBREECHO_DEV_RECOVERY_MARKER' "$KERNEL_SRC"; then
  recovery_marker_kconfig=1
  case "$IMAGE_PROFILE" in
    development) "$KERNEL_SRC/scripts/config" --file "$KERNEL_OUT/.config" -e LIBREECHO_DEV_RECOVERY_MARKER ;;
    ota) "$KERNEL_SRC/scripts/config" --file "$KERNEL_OUT/.config" -d LIBREECHO_DEV_RECOVERY_MARKER ;;
  esac
fi
make -s -C "$KERNEL_SRC" O="$KERNEL_OUT" ARCH=arm CROSS_COMPILE="$CROSS" \
  LD="${CROSS}ld.bfd" olddefconfig

# The 3.18 kernel carries the MediaTek controller and Android gadget under
# vendor symbols.  Current 6.1 uses the upstream MUSB controller and configfs
# gadget path.  Keep the contract explicit for both generations.
require_config() {
  local symbol="$1"
  local expected="$2"
  local line="CONFIG_${symbol}=${expected}"
  grep -qxF "$line" "$KERNEL_OUT/.config" || {
    echo "ERROR: required kernel config missing: $line" >&2
    exit 1
  }
}

require_any_config() {
  local expected="$1"
  shift
  local symbol
  for symbol in "$@"; do
    if grep -qxF "CONFIG_${symbol}=${expected}" "$KERNEL_OUT/.config"; then
      return 0
    fi
  done
  echo "ERROR: required kernel config missing one of: $* (expected ${expected})" >&2
  exit 1
}

require_any_config y USB_MTK_HDRC USB_MUSB_HDRC
if grep -qxF 'CONFIG_USB_G_ANDROID=y' "$KERNEL_OUT/.config"; then
  :
elif grep -qxF 'CONFIG_USB_MUSB_HDRC=y' "$KERNEL_OUT/.config"; then
  # Generic FunctionFS is not enough: without the MT8163 glue and configfs
  # functions there is no UDC to bind and neither ADB nor RNDIS can enumerate.
  require_config CONFIGFS_FS y
  require_config USB_MUSB_MEDIATEK y
  require_config USB_CONFIGFS y
  require_config USB_CONFIGFS_F_FS y
  require_config USB_CONFIGFS_RNDIS y
  require_config PINCTRL_MT8163 y
  require_config MTK_MT8163_CONSYS y
  require_config MTK_COMBO_WIFI y
  require_config LEDS_CLASS_MULTICOLOR y
  require_config LEDS_IS31FL32XX y
  require_config MTK_MT8163_BLUEZ_HCI y
  # Full-parity board closure: PMIC/reset/RTC/input/sensors/LEDs plus the
  # feature-payload and discovery primitives used by production services.
  require_config MFD_MT6397 y
  require_config REGULATOR_MT6323 y
  require_config POWER_RESET_MT6323 y
  require_config RTC_DRV_MT6397 y
  require_config KEYBOARD_MTK_PMIC y
  require_config PWM_MEDIATEK y
  require_config NVMEM_MTK_EFUSE y
  require_config IIO_ST_LSM6DSX y
  require_config AMZ_PRIVACY y
  require_config LEDS_MT6323 y
  require_config FILE_LOCKING y
  require_config INOTIFY_USER y
  require_config IP_MULTICAST y
  require_config BLK_DEV_LOOP y
  require_config SQUASHFS_LZ4 y
else
  require_config USB_CONFIGFS y
  require_config USB_CONFIGFS_F_FS y
fi
require_config AEABI y
if grep -qxF 'CONFIG_USB_MTK_HDRC=y' "$KERNEL_OUT/.config"; then
  require_config OABI_COMPAT y
fi
if (( recovery_marker_kconfig )); then
  if [[ "$IMAGE_PROFILE" == development ]]; then
    require_config LIBREECHO_DEV_RECOVERY_MARKER y
  elif ! grep -qxF '# CONFIG_LIBREECHO_DEV_RECOVERY_MARKER is not set' "$KERNEL_OUT/.config"; then
    echo "ERROR: OTA profile did not disable CONFIG_LIBREECHO_DEV_RECOVERY_MARKER" >&2
    exit 1
  fi
fi
# The Amazon MT8163 machine moved from the vendor 3.18 symbol to the
# 6.1 SPI/topology symbols.  Require both the machine and its transport.
require_any_config y MT_SND_SOC_8163_AMZN SND_SOC_MT8163_RADAR_PUFFIN
require_any_config y MT_SND_SOC_8163_AMZN SND_SOC_AMZN_MT8163_SPI_AUDIO
require_config SND_SOC_8_MICS y
if grep -qxF 'CONFIG_SND_SOC_MT8163_RADAR_PUFFIN=y' "$KERNEL_OUT/.config"; then
  require_config SND_SOC_TLV320AIC32X4 y
  require_config SND_SOC_TLV320AIC32X4_I2C y
  require_config SND_SOC_AMZN_MT8163_SPI_AUDIO y
  require_config SND_SOC_MT8163_RADAR_PUFFIN y
fi
if grep -q '^CONFIG_MT_SND_SOC_8163_AMZN_SPEAKER=y' "$KERNEL_OUT/.config"; then
  echo "ERROR: playback-only speaker closure conflicts with the full Amazon audio topology" >&2
  echo "       regenerate with: ./build.sh --defconfig" >&2
  exit 1
fi

# The Radar-Puffin 6.1 machine driver must clear the mute bit in the actual
# headphone-driver gain registers. Page-1 registers 18/19 are line-out
# gains; accepting those aliases would produce a bootable, enumerated ALSA
# card that remains acoustically silent.
radar_machine_source="$KERNEL_SRC/sound/soc/mediatek/mt8163/mt8163-radar-puffin.c"
radar_codec_header="$KERNEL_SRC/sound/soc/codecs/tlv320aic32x4.h"
radar_afe_source="$KERNEL_SRC/sound/soc/mediatek/mt8163/mt8163-afe.c"
require_source_marker() {
  local source=$1
  local marker=$2
  local label=$3
  grep -Fq -- "$marker" "$source" || {
    echo "ERROR: documented audio fix missing $label marker: $marker" >&2
    exit 1
  }
}
if [[ -f "$radar_machine_source" && -f "$radar_codec_header" &&
      -f "$radar_afe_source" ]]; then
  grep -Eq '#define[[:space:]]+RADAR_HPLGAIN[[:space:]]+RADAR_AIC32X4_REG\(1,[[:space:]]*16\)' \
    "$radar_machine_source" || {
      echo "ERROR: Radar-Puffin HPLGAIN must map to codec page 1 register 16" >&2
      exit 1
    }
  grep -Eq '#define[[:space:]]+RADAR_HPRGAIN[[:space:]]+RADAR_AIC32X4_REG\(1,[[:space:]]*17\)' \
    "$radar_machine_source" || {
      echo "ERROR: Radar-Puffin HPRGAIN must map to codec page 1 register 17" >&2
      exit 1
    }
  grep -Eq '#define[[:space:]]+AIC32X4_HPLGAIN[[:space:]]+AIC32X4_REG\(1,[[:space:]]*16\)' \
    "$radar_codec_header" || {
      echo "ERROR: TLV320AIC32x4 header does not confirm HPLGAIN register 16" >&2
      exit 1
    }
  grep -Eq '#define[[:space:]]+AIC32X4_HPRGAIN[[:space:]]+AIC32X4_REG\(1,[[:space:]]*17\)' \
    "$radar_codec_header" || {
      echo "ERROR: TLV320AIC32x4 header does not confirm HPRGAIN register 17" >&2
      exit 1
    }
  require_source_marker "$radar_machine_source" \
    'RADAR_PUFFIN_DAC_PROCESSING_BLOCK' 'PRB_P2 profile'
  require_source_marker "$radar_machine_source" \
    'radar_speaker_apply_profile(component)' 'speaker coefficients'
  require_source_marker "$radar_machine_source" \
    'RADAR_LDACVOL, 0' 'left DAC 0 dB volume'
  require_source_marker "$radar_machine_source" \
    'RADAR_RDACVOL, 0' 'right DAC 0 dB volume'
  # The accepted Linux 6.1 transport is duplicated stereo: the mono
  # programme is duplicated by userspace into L/R, and both physical HP
  # drivers must be unmuted.  The former MonoRight marker is obsolete and
  # would reject the production source that implements this policy.
  require_source_marker "$radar_machine_source" \
    'RADAR_HP_DRIVER_MUTE, 0' 'duplicated-stereo HP unmute'
  require_source_marker "$radar_afe_source" \
    'clk_set_parent(afe->aud_mux2, afe->apll2)' 'APLL2 parent selection'
  require_source_marker "$radar_afe_source" \
    'AFE_APLL2_DIV0_SEL_4' 'APLL2/4 divider programming'
  require_source_marker "$radar_afe_source" \
    'AFE_I2S_DIV1_ACTIVE_VALUE' 'AUDDIV1 programming'
  require_source_marker "$radar_afe_source" \
    'AFE_I05_TO_O03' 'DL1 left AFE route'
  require_source_marker "$radar_afe_source" \
    'AFE_I06_TO_O04' 'DL1 right AFE route'
fi

if grep -qxF 'CONFIG_USB_G_ANDROID=y' "$KERNEL_OUT/.config" &&
   grep -q '^CONFIG_USB_FUNCTIONFS=' "$KERNEL_OUT/.config"; then
  echo "ERROR: CONFIG_USB_FUNCTIONFS conflicts with CONFIG_USB_G_ANDROID in this kernel" >&2
  exit 1
fi

echo "=== building ARM32 zImage and Radar-Puffin DTB (-j$JOBS) ==="
make -C "$KERNEL_SRC" O="$KERNEL_OUT" ARCH=arm CROSS_COMPILE="$CROSS" \
  LD="${CROSS}ld.bfd" -j"$JOBS" zImage libreecho-radar-puffin.dtb

ZIMAGE_BUILD="$KERNEL_OUT/arch/arm/boot/zImage"
SYSMAP_BUILD="$KERNEL_OUT/System.map"
KERNEL_DTB="$KERNEL_OUT/arch/arm/boot/dts/libreecho-radar-puffin.dtb"
[[ -f "$ZIMAGE_BUILD" && -f "$SYSMAP_BUILD" && -f "$KERNEL_DTB" ]] || {
  echo "ERROR: kernel or DTB outputs missing" >&2; exit 1;
}
DTB_VERIFIER="$TOOLS_DIR/verify_radar_puffin_dtb.py"
[[ -f "$DTB_VERIFIER" && ! -L "$DTB_VERIFIER" ]] || {
  echo "ERROR: Radar-Puffin DTB verifier missing: $DTB_VERIFIER" >&2
  exit 1
}
python3 -B "$DTB_VERIFIER" --dtb "$KERNEL_DTB"

zsha="$(sha256sum "$ZIMAGE_BUILD" | awk '{print $1}')"
mapsha="$(sha256sum "$SYSMAP_BUILD" | awk '{print $1}')"
if [[ -n "$KERNEL_ZIMAGE_OVERRIDE" || -n "$KERNEL_SYSTEM_MAP_OVERRIDE" ]]; then
  [[ -n "$KERNEL_ZIMAGE_OVERRIDE" && -n "$KERNEL_ZIMAGE_OVERRIDE_SHA256" &&
     -n "$KERNEL_SYSTEM_MAP_OVERRIDE" && -n "$KERNEL_SYSTEM_MAP_OVERRIDE_SHA256" ]] || {
    echo "ERROR: kernel parity requires zImage/System.map paths and hashes together" >&2; exit 1
  }
  [[ "$(sha256sum "$KERNEL_ZIMAGE_OVERRIDE" | awk '{print $1}')" == "$KERNEL_ZIMAGE_OVERRIDE_SHA256" ]] || {
    echo "ERROR: kernel zImage parity override hash mismatch" >&2; exit 1
  }
  [[ "$(sha256sum "$KERNEL_SYSTEM_MAP_OVERRIDE" | awk '{print $1}')" == "$KERNEL_SYSTEM_MAP_OVERRIDE_SHA256" ]] || {
    echo "ERROR: kernel System.map parity override hash mismatch" >&2; exit 1
  }
  zsha="$KERNEL_ZIMAGE_OVERRIDE_SHA256"
  mapsha="$KERNEL_SYSTEM_MAP_OVERRIDE_SHA256"
fi
dtbsha="$(sha256sum "$KERNEL_DTB" | awk '{print $1}')"
head="$(git -C "$KERNEL_SRC" rev-parse --short=12 HEAD 2>/dev/null || echo nogit)"
ui_commit="$(git -C "$UI_SOURCE" rev-parse HEAD)"
ui_diff_sha="$(source_state_sha256 "$UI_SOURCE")"
dirty=clean
[[ -z "$(git -C "$KERNEL_SRC" status --porcelain)" ]] || dirty=dirty
kernel_diffsha="$(source_state_sha256 "$KERNEL_SRC")"
tooling_head="$(git -C "$TOOLING_SRC" rev-parse HEAD)"
tooling_state=clean
[[ -z "$(git -C "$TOOLING_SRC" status --porcelain)" ]] || tooling_state=dirty
tooling_diffsha="$(source_state_sha256 "$TOOLING_SRC")"
build_head="$(git -C "$PIPELINE" rev-parse HEAD)"
build_state=clean
[[ -z "$(git -C "$PIPELINE" status --porcelain)" ]] || build_state=dirty
build_diffsha="$(source_state_sha256 "$PIPELINE")"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-${head}-${dirty}-${IMAGE_PROFILE}-${SERVICE_PROFILE}-${UPDATE_CHANNEL}-${policy_token}-ssh${SSH_ENABLED}-ui${ui_commit:0:8}-${zsha:0:8}"
RUN="$RUNS/$run_id"
mkdir -p "$RUN/components"
COMPONENTS_MANIFEST="$RUN/components.json"
# Every cacheable output is copied into the immutable run before it is used by
# image aggregation or provenance.  Shared generated/cache paths never escape
# this materialization boundary.
component_materialize busybox "$busybox_cache_key" "$busybox_status" "$BUSYBOX_OUTPUT" "$RUN/components/busybox"
component_materialize musl "$musl_cache_key" "$musl_status" "$MUSL_OUTPUT" "$RUN/components/musl"
component_materialize wpa-supplicant "$wpa_cache_key" "$wpa_status" "$WPA_OUTPUT" "$RUN/components/wpa-supplicant"
component_materialize connectivity "$connectivity_cache_key" "$connectivity_status" "$CONNECTIVITY_HELPERS" "$RUN/components/connectivity"
component_materialize audio-tools "$audio_tools_cache_key" "$audio_tools_status" "$AUDIO_TOOLS_DIR" "$RUN/components/audio-tools"
BUSYBOX_OUTPUT="$RUN/components/busybox"
MUSL_OUTPUT="$RUN/components/musl"
MUSL_LOADER="$MUSL_OUTPUT/ld-musl-armhf.so.1"
WPA_OUTPUT="$RUN/components/wpa-supplicant"
WPA_SUPPLICANT="$WPA_OUTPUT/wpa_supplicant"
CONNECTIVITY_HELPERS="$RUN/components/connectivity"
AUDIO_TOOLS_DIR="$RUN/components/audio-tools"
install -m 0644 "$BUSYBOX_OUTPUT/busybox-source.json" "$RUN/busybox-source.json"
install -m 0644 "$MUSL_OUTPUT/musl-source.json" "$RUN/musl-source.json"
install -m 0644 "$WPA_OUTPUT/wpa-supplicant-source.json" "$RUN/wpa-supplicant-source.json"
install -m 0644 "$CONNECTIVITY_HELPERS/connectivity-source.json" "$RUN/connectivity-source.json"
install -m 0644 "$AUDIO_TOOLS_DIR/tinyalsa-source.json" "$RUN/tinyalsa-source.json"
install -m 0644 "$GENERATED_ROOT/busybox-build.log" "$RUN/busybox-build.log"
install -m 0644 "$GENERATED_ROOT/musl-build.log" "$RUN/musl-build.log"
install -m 0644 "$GENERATED_ROOT/wpa-supplicant-build.log" "$RUN/wpa-supplicant-build.log"
install -m 0644 "$GENERATED_ROOT/connectivity-build.log" "$RUN/connectivity-build.log"
install -m 0644 "$GENERATED_ROOT/audio-tools-build.log" "$RUN/audio-tools-build.log"
if [[ -n "$KERNEL_ZIMAGE_OVERRIDE" || -n "$KERNEL_SYSTEM_MAP_OVERRIDE" ]]; then
  [[ -n "$KERNEL_ZIMAGE_OVERRIDE" && -n "$KERNEL_ZIMAGE_OVERRIDE_SHA256" &&
     -n "$KERNEL_SYSTEM_MAP_OVERRIDE" && -n "$KERNEL_SYSTEM_MAP_OVERRIDE_SHA256" ]] || {
    echo "ERROR: kernel parity requires zImage/System.map paths and hashes together" >&2; exit 1
  }
  [[ "$(sha256sum "$KERNEL_ZIMAGE_OVERRIDE" | awk '{print $1}')" == "$KERNEL_ZIMAGE_OVERRIDE_SHA256" ]] || {
    echo "ERROR: kernel zImage parity override hash mismatch" >&2; exit 1
  }
  [[ "$(sha256sum "$KERNEL_SYSTEM_MAP_OVERRIDE" | awk '{print $1}')" == "$KERNEL_SYSTEM_MAP_OVERRIDE_SHA256" ]] || {
    echo "ERROR: kernel System.map parity override hash mismatch" >&2; exit 1
  }
  cp -- "$KERNEL_ZIMAGE_OVERRIDE" "$RUN/zImage"
  cp -- "$KERNEL_SYSTEM_MAP_OVERRIDE" "$RUN/System.map"
  zsha="$KERNEL_ZIMAGE_OVERRIDE_SHA256"
  mapsha="$KERNEL_SYSTEM_MAP_OVERRIDE_SHA256"
else
  cp -- "$ZIMAGE_BUILD" "$RUN/zImage"
  cp -- "$SYSMAP_BUILD" "$RUN/System.map"
fi
cp -- "$KERNEL_DTB" "$RUN/libreecho-radar-puffin.dtb"
cp -- "$KERNEL_OUT/.config" "$RUN/kernel.config"
kernel_config_sha="$(sha256sum "$RUN/kernel.config" | awk '{print $1}')"
echo "kernel_config_sha256=$kernel_config_sha"

echo "=== rebuilding or restoring static ARM32 OTA control library ==="
LIBSODIUM_BUILDER="$TOOLS_DIR/ota/build_libsodium.sh"
[[ -x "$LIBSODIUM_BUILDER" ]] || { echo "ERROR: libsodium builder is missing or not executable" >&2; exit 1; }
libsodium_cache_key="$(component_cache_key libsodium \
  --tree "platform-ota=$TOOLS_DIR/ota" --value "toolchain=$CORE_TOOLCHAIN_KEY" \
  --file "source-archive=$LIBSODIUM_SOURCE_ARCHIVE")"
libsodium_status=rebuilt
if ! component_cache_restore libsodium "$libsodium_cache_key" "$LIBSODIUM_OUTPUT"; then
  "$LIBSODIUM_BUILDER" --archive "$LIBSODIUM_SOURCE_ARCHIVE" --output "$LIBSODIUM_OUTPUT" \
    --cc "$OTA_MUSL_CC" --ar "${MUSL_CROSS_PREFIX}ar" --ranlib "${MUSL_CROSS_PREFIX}ranlib" \
    --sysroot "$OTA_MUSL_SYSROOT" --native-root "$OTA_MUSL_NATIVE_ROOT" | tee "$RUN/libsodium-build.log"
  component_cache_store libsodium "$libsodium_cache_key" "$LIBSODIUM_OUTPUT"
else
  libsodium_status=hit
  printf 'component_cache_hit=libsodium\n' >"$RUN/libsodium-build.log"
fi
component_materialize libsodium "$libsodium_cache_key" "$libsodium_status" "$LIBSODIUM_OUTPUT" "$RUN/components/libsodium"
LIBSODIUM_OUTPUT="$RUN/components/libsodium"
OTA_SODIUM_ROOT="$LIBSODIUM_OUTPUT"
OTA_SODIUM_A="$OTA_SODIUM_ROOT/lib/libsodium.a"
install -m 0644 "$LIBSODIUM_OUTPUT/libsodium-source.json" "$RUN/libsodium-source.json"
OTA_CONTROL_STAGE="$RUN/ota-control-stage"
ota_control_cache_key="$(component_cache_key ota-control \
  --tree "platform-ota=$OTA_DIR" --value "core-toolchain=$CORE_TOOLCHAIN_KEY" \
  --tree "libsodium=$OTA_SODIUM_ROOT" --file "ota-musl-cc=$OTA_MUSL_CC" \
  --file "audio-cc=$AUDIO_CC" --file "public-key=$OTA_PUBLIC_KEY" \
  --value "target=arm32-static")"
ota_control_status=rebuilt
rm -rf "$OTA_CONTROL_STAGE" "$RUN/components/ota-control"
if ! component_cache_restore ota-control "$ota_control_cache_key" "$OTA_CONTROL_STAGE"; then
  mkdir -p "$OTA_CONTROL_STAGE"
  env LD_LIBRARY_PATH="$OTA_MUSL_NATIVE_ROOT/usr/lib" \
    "$OTA_MUSL_CC" --sysroot="$OTA_MUSL_SYSROOT" \
    -Os -ffunction-sections -fdata-sections -Wl,--gc-sections,-s \
    -Wall -Wextra -Werror \
    -o "$OTA_CONTROL_STAGE/libreecho-bootctl" "$OTA_DIR/libreecho_bootctl.c"
  env LD_LIBRARY_PATH="$OTA_MUSL_NATIVE_ROOT/usr/lib:$OTA_MUSL_NATIVE_ROOT/lib" \
    "$AUDIO_CC" --sysroot="$OTA_MUSL_SYSROOT" \
    -Os -ffunction-sections -fdata-sections -static -no-pie \
    -Wl,--gc-sections,-s -Wall -Wextra -Werror \
    -I"$OTA_SODIUM_ROOT/include" -o "$OTA_CONTROL_STAGE/libreecho-update-verify" \
    "$OTA_DIR/libreecho_update_verify.c" "$OTA_SODIUM_A" -lpthread
  component_cache_store ota-control "$ota_control_cache_key" "$OTA_CONTROL_STAGE"
else
  ota_control_status=hit
fi
component_materialize ota-control "$ota_control_cache_key" "$ota_control_status" \
  "$OTA_CONTROL_STAGE" "$RUN/components/ota-control"
rm -rf "$OTA_CONTROL_STAGE"
OTA_CONTROL_OUTPUT="$RUN/components/ota-control"
OTA_BOOTCTL="$OTA_CONTROL_OUTPUT/libreecho-bootctl"
OTA_VERIFIER="$OTA_CONTROL_OUTPUT/libreecho-update-verify"
[[ -f "$OTA_BOOTCTL" && -f "$OTA_VERIFIER" ]] || {
  echo "ERROR: OTA control cache payload is incomplete" >&2; exit 1
}
ota_bootctl_sha="$(sha256sum "$OTA_BOOTCTL" | awk '{print $1}')"
ota_verifier_sha="$(sha256sum "$OTA_VERIFIER" | awk '{print $1}')"
ota_public_key_sha="$(sha256sum "$OTA_PUBLIC_KEY" | awk '{print $1}')"

echo "=== building or restoring source-pinned ARM32 adbd ==="
[[ -x "$ADBD_BUILDER" ]] || { echo "ERROR: adbd builder is missing or not executable" >&2; exit 1; }
adbd_cache_key="$(component_cache_key adbd \
  --tree "aosp-system-core=$ADBD_SOURCE" --tree "linux-uapi=$ADBD_KERNEL_HEADERS" \
  --value "core-toolchain=$CORE_TOOLCHAIN_KEY" --file "builder=$ADBD_BUILDER" \
  --file "ota-musl-cc=$OTA_MUSL_CC" --value "target=arm32-static")"
ADBD_STAGE="$RUN/adbd-stage"
adbd_status=rebuilt
rm -rf "$ADBD_STAGE" "$RUN/components/adbd"
if ! component_cache_restore adbd "$adbd_cache_key" "$ADBD_STAGE"; then
  mkdir -p "$ADBD_STAGE"
  env LD_LIBRARY_PATH="$OTA_MUSL_NATIVE_ROOT/usr/lib" \
    "$ADBD_BUILDER" --source "$ADBD_SOURCE" --output "$ADBD_STAGE" \
    --cc "$OTA_MUSL_CC" --sysroot "$OTA_MUSL_SYSROOT" \
    --kernel-headers "$ADBD_KERNEL_HEADERS" | tee "$RUN/adbd-build.log"
  component_cache_store adbd "$adbd_cache_key" "$ADBD_STAGE"
else
  adbd_status=hit
  printf 'component_cache_hit=adbd\n' >"$RUN/adbd-build.log"
fi
component_materialize adbd "$adbd_cache_key" "$adbd_status" \
  "$ADBD_STAGE" "$RUN/components/adbd"
rm -rf "$ADBD_STAGE"
ADBD_OUTPUT="$RUN/components/adbd"
ADBD_BINARY="$ADBD_OUTPUT/adbd"
ADBD_METADATA="$ADBD_OUTPUT/adbd-source.json"
[[ -f "$ADBD_BINARY" && -f "$ADBD_METADATA" ]] || {
  echo "ERROR: source-built adbd output is incomplete" >&2; exit 1;
}
adbd_sha="$(sha256sum "$ADBD_BINARY" | awk '{print $1}')"
echo "adbd_sha256=$adbd_sha"

echo "=== building static ARM32 LibreEcho-UI bundle ==="
UI_BUILDER="$TOOLS_DIR/ui/build_ui_bundle.sh"
[[ -x "$UI_BUILDER" ]] || {
  echo "ERROR: UI bundle builder is missing or not executable: $UI_BUILDER" >&2
  exit 1
}
UI_TOOLCHAIN_KEY="$(component_cache_key ui-armhf-toolchain \
  --value "target=arm-linux-gnueabihf" \
  --file "gcc=${UI_CROSS}gcc" --file "gxx=${UI_CROSS}g++" \
  --file "ar=${UI_CROSS}ar" --file "ranlib=${UI_CROSS}ranlib" \
  --file "strip=${UI_CROSS}strip" --file "nm=${UI_CROSS}nm" \
  --file "ld=${UI_CROSS}ld" --file "objcopy=${UI_CROSS}objcopy" \
  --file "readelf=${UI_CROSS}readelf" \
  --tree "runtime-sysroot=$CORE_RUNTIME_SYSROOT" \
  --tree "gcc-runtime=$CORE_GCC_LIBDIR")"
record_component_identity ui-armhf-toolchain "$UI_TOOLCHAIN_KEY"
ui_bundle_cache_key="$(component_cache_key ui-bundle \
  --value "payload_layout=bundle-relink-v1" \
  --value "ui_head=$ui_commit" --value "ui_diff=$ui_diff_sha" \
  --value "ui_toolchain=$UI_TOOLCHAIN_KEY" \
  --file "builder=$UI_BUILDER" \
  --file "ui-musl-gcc=$AUDIO_CC" \
  --value "cross_target=armhf" --value "musl_cc_target=armhf" \
  --value "core-toolchain=$CORE_TOOLCHAIN_KEY" --value "service_profile=$SERVICE_PROFILE")"
UI_BUNDLE_STAGE="$RUN/ui-bundle-stage"
rm -rf "$UI_BUNDLE_STAGE" "$RUN/components/ui-bundle"
ui_bundle_status=rebuilt
if ! component_cache_restore ui-bundle "$ui_bundle_cache_key" "$UI_BUNDLE_STAGE"; then
  mkdir -p "$UI_BUNDLE_STAGE"
  LIBREECHO_UI_CROSS_COMPILE="$UI_CROSS" \
  LIBREECHO_UI_MUSL_NATIVE_ROOT="$OTA_MUSL_NATIVE_ROOT" \
  LIBREECHO_UI_MUSL_SYSROOT="$OTA_MUSL_SYSROOT" \
  LIBREECHO_UI_MUSL_CC="$AUDIO_CC" \
  LIBREECHO_UI_MUSL_NATIVE_LIB="$OTA_MUSL_NATIVE_ROOT/usr/lib" \
    "$UI_BUILDER" "$UI_SOURCE" "$UI_BUNDLE_STAGE/bundle" | tee "$RUN/ui-build.log"
  mkdir -p "$UI_BUNDLE_STAGE/relink"
  ui_relink_count=0
  while IFS= read -r -d '' ui_relink_input; do
    ui_relink_relative="${ui_relink_input#"$UI_SOURCE/build/"}"
    install -D -m 0644 "$ui_relink_input" \
      "$UI_BUNDLE_STAGE/relink/$ui_relink_relative"
    ui_relink_count=$((ui_relink_count + 1))
  done < <(find "$UI_SOURCE/build" -type f \( -name '*.o' -o -name '*.a' \) -print0)
  ((ui_relink_count > 0)) || {
    echo "ERROR: UI bundle build produced no relink objects" >&2
    exit 1
  }
  require_relink_tree "$UI_BUNDLE_STAGE/relink" "UI bundle"
  component_cache_store ui-bundle "$ui_bundle_cache_key" "$UI_BUNDLE_STAGE"
else
  ui_bundle_status=hit
  printf 'component_cache_hit=ui-bundle\n' >"$RUN/ui-build.log"
fi
component_materialize ui-bundle "$ui_bundle_cache_key" "$ui_bundle_status" \
  "$UI_BUNDLE_STAGE" "$RUN/components/ui-bundle"
rm -rf "$UI_BUNDLE_STAGE"
UI_BUNDLE="$RUN/components/ui-bundle/bundle"
UI_RELINK="$RUN/components/ui-bundle/relink"
UI_MANIFEST="$UI_BUNDLE/share/libreecho/ui-manifest.txt"
[[ -f "$UI_MANIFEST" && -d "$UI_RELINK" ]] || {
  echo "ERROR: UI cache payload is missing bundle or relink output" >&2
  exit 1
}
require_relink_tree "$UI_RELINK" "UI bundle"
# build_ui_bundle records the source identity captured before its clean build.
# Preserve that same pre-build pin from run creation; recapturing afterward
# would hash generated untracked objects rather than the staged source state.
ui_manifest_sha="$(sha256sum "$UI_MANIFEST" | awk '{print $1}')"
echo "ui_commit=$ui_commit"
echo "ui_diff_sha256=$ui_diff_sha"
echo "ui_manifest_sha256=$ui_manifest_sha"
# Feature daemons must never inherit arbitrary objects from the external UI
# checkout after a UI-bundle cache hit.  Rebuild their dependency graph from a
# clean generated tree, then snapshot the final relink closure into this run.
rm -rf "$UI_SOURCE/build"

AGENT_DAEMON=
TTS_DAEMON=
WAKE_DAEMON=
STT_DAEMON=
ASSISTANT_CURL=
ASSISTANT_CURL_LICENSE=
if [[ "$FEATURES_ENABLED" == 1 ]]; then
echo "=== building static ARM32 streamed assistant daemon ==="
make -C "$UI_SOURCE" CROSS_COMPILE="$UI_CROSS" CC=gcc LDFLAGS=-static \
  build/libreecho-agentd | tee "$RUN/assistant-daemon-build.log"
AGENT_DAEMON="$UI_SOURCE/build/libreecho-agentd"
[[ -f "$AGENT_DAEMON" && ! -L "$AGENT_DAEMON" ]] || {
  echo "ERROR: assistant daemon build did not produce the binary" >&2
  exit 1
}

echo "=== building static ARM32 neural TTS daemon ==="
make -C "$UI_SOURCE" CROSS_COMPILE="$UI_CROSS" CC=gcc \
  SHERPA_PREFIX="$SHERPA_PREFIX" ORT_BUILD="$ORT_BUILD" ORT_PREFIX="$ORT_PREFIX" \
  RE2_ARCHIVE="$ORT_PREFIX/lib/libre2.a" \
  ESPEAK_SRC="$ESPEAK_SOURCE" FLITE_SRC="$FLITE_SOURCE" \
  SPEEX_PREFIX="$SPEEX_PREFIX" ARM_SPEEX_PREFIX="$SPEEX_PREFIX" \
  build/libreecho-ttsd-sherpa | tee "$RUN/tts-daemon-build.log"
TTS_DAEMON="$UI_SOURCE/build/libreecho-ttsd-sherpa"
[[ -f "$TTS_DAEMON" && ! -L "$TTS_DAEMON" ]] || {
  echo "ERROR: neural TTS build did not produce the daemon" >&2
  exit 1
}

if [[ "$WAKEWORD_ENABLED" == 1 ]]; then
  echo "=== restoring or building reduced ARM32 wakeword dependencies ==="
  WAKE_RUNTIME_BUILDER="$TOOLS_DIR/wakeword/build_runtime.sh"
  [[ -x "$WAKE_RUNTIME_BUILDER" ]] || {
    echo "ERROR: wakeword runtime builder is missing: $WAKE_RUNTIME_BUILDER" >&2
    exit 1
  }
  wake_ort_head="$(git -C "$WAKE_ORT_SOURCE" rev-parse HEAD)"
  wake_ort_diff="$(source_state_sha256 "$WAKE_ORT_SOURCE")"
  wake_ort_cache_key="$(component_cache_key wake-ort \
    --value "contract=reduced-static-archives-v5-abseil-closure" \
    --value "jobs=$JOBS" --value "ui_toolchain=$UI_TOOLCHAIN_KEY" \
    --value "ort_head=$wake_ort_head" --value "ort_diff=$wake_ort_diff" \
    --tree "platform-wakeword=$TOOLS_DIR/wakeword" \
    --tree "flatbuffers-python=$WAKE_FLATBUFFERS_PYTHON" \
    --file "operators-config=$TOOLS_DIR/wakeword/required_operators.config" \
    --file "mel-model=$WAKE_MEL_MODEL" --file "embedding-model=$WAKE_EMBEDDING_MODEL" \
    --file "classifier-model=$WAKE_CLASSIFIER_MODEL" \
    --file "host-cmake=$(command -v cmake)" --file "host-make=$(command -v make)" \
    --file "host-python=$(command -v python3)" --file "host-autoconf=$(command -v autoconf)" \
    --file "host-automake=$(command -v automake)" \
    --file "host-libtoolize=$(command -v libtoolize)" \
    --file "host-flock=$(command -v flock)")"
  wake_runtime_cache_key="$(component_cache_key wake-runtime \
    --value "contract=daemon-relink-speex-v2" --value "wake_ort=$wake_ort_cache_key" \
    --value "jobs=$JOBS" --value "ui_toolchain=$UI_TOOLCHAIN_KEY" \
    --value "ui_head=$ui_commit" --value "ui_diff=$ui_diff_sha" \
    --tree "platform-wakeword=$TOOLS_DIR/wakeword" \
    --tree "flatbuffers-python=$WAKE_FLATBUFFERS_PYTHON" \
    --file "speex-source=$WAKE_SPEEX_ARCHIVE" --file "re2-archive=$ORT_PREFIX/lib/libre2.a" \
    --file "ui-makefile=$UI_SOURCE/Makefile" \
    --file "operators-config=$TOOLS_DIR/wakeword/required_operators.config" \
    --file "mel-model=$WAKE_MEL_MODEL" --file "embedding-model=$WAKE_EMBEDDING_MODEL" \
    --file "classifier-model=$WAKE_CLASSIFIER_MODEL" \
    --file "host-cmake=$(command -v cmake)" --file "host-make=$(command -v make)" \
    --file "host-python=$(command -v python3)" --file "host-autoconf=$(command -v autoconf)" \
    --file "host-automake=$(command -v automake)" \
    --file "host-libtoolize=$(command -v libtoolize)" \
    --file "host-flock=$(command -v flock)")"

  WAKE_ORT_WORK="$RUN/wake-ort-working"
  WAKE_RUNTIME_STAGE="$RUN/wake-runtime-stage"
  WAKE_BUILD_ROOT="$RUN/wake-runtime-build"
  rm -rf "$WAKE_ORT_WORK" "$WAKE_RUNTIME_STAGE" "$WAKE_BUILD_ROOT" \
    "$RUN/components/wake-ort" "$RUN/components/wake-runtime"
  wake_ort_status=rebuilt
  wake_ort_ready=0
  if component_cache_restore wake-ort "$wake_ort_cache_key" "$WAKE_ORT_WORK"; then
    wake_ort_status=hit
    wake_ort_ready=1
  fi

  wake_runtime_status=rebuilt
  wake_runtime_ready=0
  if [[ "$wake_ort_ready" == 1 ]] && \
      component_cache_restore wake-runtime "$wake_runtime_cache_key" "$WAKE_RUNTIME_STAGE"; then
    wake_runtime_status=hit
    wake_runtime_ready=1
    printf 'component_cache_hit=wake-runtime\n' >"$RUN/wakeword-daemon-build.log"
  fi

  if [[ "$wake_runtime_ready" != 1 ]]; then
    mkdir -p "$WAKE_BUILD_ROOT/wakeword"
    if [[ "$wake_ort_ready" == 1 ]]; then
      WAKE_ORT_BUILD="$WAKE_ORT_WORK"
    else
      WAKE_ORT_BUILD="$WAKE_BUILD_ROOT/onnxruntime-wake-reduced"
    fi
    WAKE_SPEEX_BUILD="$WAKE_BUILD_ROOT/speexdsp-arm32"
    WAKE_DAEMON_BUILD="$WAKE_BUILD_ROOT/wakeword/libreecho-waked"
    LIBREECHO_WAKE_CROSS="$UI_CROSS" JOBS="$JOBS" \
      LIBREECHO_WAKE_FLATBUFFERS_PYTHON="$WAKE_FLATBUFFERS_PYTHON" \
      LIBREECHO_WAKE_RE2_ARCHIVE="$ORT_PREFIX/lib/libre2.a" \
      LIBREECHO_WAKE_RELINK_OUTPUT="$WAKE_BUILD_ROOT/wakeword/relink" \
      "$WAKE_RUNTIME_BUILDER" "$UI_SOURCE" "$WAKE_ORT_SOURCE" \
      "$WAKE_ORT_BUILD" "$WAKE_SPEEX_ARCHIVE" "$WAKE_SPEEX_BUILD" \
      "$WAKE_DAEMON_BUILD" | tee "$RUN/wakeword-daemon-build.log"
    [[ -f "$WAKE_DAEMON_BUILD" && ! -L "$WAKE_DAEMON_BUILD" ]] || {
      echo "ERROR: wakeword runtime build did not produce the daemon" >&2
      exit 1
    }

    mkdir -p "$WAKE_RUNTIME_STAGE/wakeword"
    cp -a -- "$WAKE_BUILD_ROOT/wakeword/." "$WAKE_RUNTIME_STAGE/wakeword/"
    cp -a -- "$WAKE_SPEEX_BUILD" "$WAKE_RUNTIME_STAGE/speex-prefix"
    require_relink_tree "$WAKE_RUNTIME_STAGE/wakeword/relink" "wakeword"
    component_cache_store wake-runtime "$wake_runtime_cache_key" "$WAKE_RUNTIME_STAGE"

    if [[ "$wake_ort_ready" != 1 ]]; then
      mkdir -p "$WAKE_ORT_WORK"
      for archive in \
          libonnxruntime_session.a libonnxruntime_optimizer.a \
          libonnxruntime_providers.a libonnxruntime_graph.a \
          libonnxruntime_framework.a libonnxruntime_common.a \
          libonnxruntime_mlas.a libonnxruntime_util.a \
          libonnxruntime_flatbuffers.a libonnxruntime_lora.a; do
        [[ -f "$WAKE_ORT_BUILD/$archive" && ! -L "$WAKE_ORT_BUILD/$archive" ]] || {
          echo "ERROR: reduced ONNX Runtime archive is missing: $archive" >&2
          exit 1
        }
        install -m 0644 "$WAKE_ORT_BUILD/$archive" "$WAKE_ORT_WORK/$archive"
      done
      # The wakeword daemon relink also consumes the reduced ORT dependency
      # archives (onnx, protobuf-lite, flatbuffers, and Abseil). They must be
      # part of the cached wake-ort payload; otherwise a wake-ort cache hit
      # combined with a wake-runtime cache miss relinks against an incomplete
      # tree.
      for dep_archive in \
          _deps/onnx-build/libonnx.a \
          _deps/onnx-build/libonnx_proto.a \
          _deps/protobuf-build/libprotobuf-lite.a \
          _deps/flatbuffers-build/libflatbuffers.a; do
        [[ -f "$WAKE_ORT_BUILD/$dep_archive" && \
           ! -L "$WAKE_ORT_BUILD/$dep_archive" ]] || {
          echo "ERROR: reduced ONNX Runtime dependency archive is missing: $dep_archive" >&2
          exit 1
        }
        mkdir -p "$WAKE_ORT_WORK/$(dirname -- "$dep_archive")"
        install -m 0644 "$WAKE_ORT_BUILD/$dep_archive" \
          "$WAKE_ORT_WORK/$dep_archive"
      done
      absl_count=0
      while IFS= read -r -d '' absl_archive; do
        relative=${absl_archive#"$WAKE_ORT_BUILD/"}
        mkdir -p "$WAKE_ORT_WORK/$(dirname -- "$relative")"
        install -m 0644 "$absl_archive" "$WAKE_ORT_WORK/$relative"
        absl_count=$((absl_count + 1))
      done < <(find "$WAKE_ORT_BUILD/_deps/abseil_cpp-build" -type f -name '*.a' -print0 | LC_ALL=C sort -z)
      ((absl_count > 0)) || {
        echo "ERROR: reduced ONNX Runtime Abseil closure is missing" >&2
        exit 1
      }
      component_cache_store wake-ort "$wake_ort_cache_key" "$WAKE_ORT_WORK"
    else
      rm -f -- "$WAKE_ORT_WORK/.onnxruntime-source" "$WAKE_ORT_WORK/provider-symbols.txt"
    fi
  fi

  component_materialize wake-ort "$wake_ort_cache_key" "$wake_ort_status" \
    "$WAKE_ORT_WORK" "$RUN/components/wake-ort"
  component_materialize wake-runtime "$wake_runtime_cache_key" "$wake_runtime_status" \
    "$WAKE_RUNTIME_STAGE" "$RUN/components/wake-runtime"
  rm -rf "$WAKE_ORT_WORK" "$WAKE_RUNTIME_STAGE" "$WAKE_BUILD_ROOT"
  WAKE_ORT_BUILD="$RUN/components/wake-ort"
  WAKE_SPEEX_PREFIX="$RUN/components/wake-runtime/speex-prefix"
  WAKE_DAEMON="$RUN/components/wake-runtime/wakeword/libreecho-waked"
  [[ -f "$WAKE_DAEMON" && ! -L "$WAKE_DAEMON" && \
     -d "$RUN/components/wake-runtime/wakeword/relink" ]] || {
    echo "ERROR: materialized wakeword runtime is incomplete" >&2
    exit 1
  }
  require_relink_tree "$RUN/components/wake-runtime/wakeword/relink" "wakeword"
fi

echo "=== building static ARM32 English streaming STT daemon ==="
make -C "$UI_SOURCE" CROSS_COMPILE="$UI_CROSS" CC=gcc \
  SHERPA_PREFIX="$SHERPA_PREFIX" ORT_BUILD="$ORT_BUILD" ORT_PREFIX="$ORT_PREFIX" \
  RE2_ARCHIVE="$ORT_PREFIX/lib/libre2.a" \
  ESPEAK_SRC="$ESPEAK_SOURCE" FLITE_SRC="$FLITE_SOURCE" \
  SPEEX_PREFIX="$SPEEX_PREFIX" ARM_SPEEX_PREFIX="$SPEEX_PREFIX" \
  build/libreecho-sttd-sherpa-arm32 | tee "$RUN/stt-daemon-build.log"
STT_DAEMON="$UI_SOURCE/build/libreecho-sttd-sherpa-arm32"
[[ -f "$STT_DAEMON" && ! -L "$STT_DAEMON" ]] || {
  echo "ERROR: streaming STT build did not produce the daemon" >&2
  exit 1
}

echo "=== snapshotting run-local UI daemon relink closure ==="
UI_DAEMON_RELINK_STAGE="$RUN/ui-daemon-relink-stage"
rm -rf "$UI_DAEMON_RELINK_STAGE" "$RUN/components/ui-daemon-relink"
mkdir -p "$UI_DAEMON_RELINK_STAGE"
ui_daemon_relink_count=0
while IFS= read -r -d '' object; do
  relative=${object#"$UI_SOURCE/build/"}
  install -D -m 0644 "$object" "$UI_DAEMON_RELINK_STAGE/$relative"
  ui_daemon_relink_count=$((ui_daemon_relink_count + 1))
done < <(find "$UI_SOURCE/build" -type f \( -name '*.o' -o -name '*.a' \) -print0 | LC_ALL=C sort -z)
((ui_daemon_relink_count > 0)) || {
  echo "ERROR: UI daemon builds produced no relink objects" >&2
  exit 1
}
ui_daemon_relink_key="$(component_cache_key ui-daemon-relink \
  --value "ui_head=$ui_commit" --value "ui_diff=$ui_diff_sha" \
  --tree "ui-daemon-relink=$UI_DAEMON_RELINK_STAGE")"
component_materialize ui-daemon-relink "$ui_daemon_relink_key" rebuilt \
  "$UI_DAEMON_RELINK_STAGE" "$RUN/components/ui-daemon-relink"
rm -rf "$UI_DAEMON_RELINK_STAGE"

echo "=== building pinned minimal static ARM32 HTTPS client ==="
ASSISTANT_CURL_BUILDER="$TOOLS_DIR/assistant/build_curl.sh"
[[ -x "$ASSISTANT_CURL_BUILDER" ]] || {
  echo "ERROR: assistant curl builder is missing: $ASSISTANT_CURL_BUILDER" >&2
  exit 1
}
AIRPLAY_SYSROOT_KEY="$("$PIPELINE/ci/airplay-sysroot-key.sh" "$AIRPLAY_SYSROOT")"
record_component_identity airplay-sysroot "$AIRPLAY_SYSROOT_KEY"
ASSISTANT_CURL_STAGE="$RUN/assistant-curl-stage"
assistant_curl_cache_key="$(component_cache_key assistant-curl \
  --tree "platform-assistant=$TOOLS_DIR/assistant" --file "source-archive=$ASSISTANT_CURL_SOURCE" \
  --value "airplay-sysroot=$AIRPLAY_SYSROOT_KEY" --file "cross-gcc=${UI_CROSS}gcc" \
  --file "cross-strip=${UI_CROSS}strip")"
assistant_curl_status=rebuilt
rm -rf "$ASSISTANT_CURL_STAGE" "$RUN/components/assistant-curl"
if ! component_cache_restore assistant-curl "$assistant_curl_cache_key" "$ASSISTANT_CURL_STAGE"; then
  mkdir -p "$ASSISTANT_CURL_STAGE"
  JOBS="$JOBS" LIBREECHO_ASSISTANT_CROSS="$UI_CROSS" \
    LIBREECHO_ASSISTANT_RELINK_OUTPUT="$ASSISTANT_CURL_STAGE/relink" \
    "$ASSISTANT_CURL_BUILDER" "$ASSISTANT_CURL_SOURCE" \
    "$AIRPLAY_SYSROOT" "$ASSISTANT_CURL_STAGE/libreecho-curl" \
    "$ASSISTANT_CURL_STAGE/curl-COPYING" \
    | tee "$RUN/assistant-curl-build.log"
  require_relink_tree "$ASSISTANT_CURL_STAGE/relink" "assistant curl"
  component_cache_store assistant-curl "$assistant_curl_cache_key" "$ASSISTANT_CURL_STAGE"
else
  assistant_curl_status=hit
  printf 'component_cache_hit=assistant-curl\n' >"$RUN/assistant-curl-build.log"
fi
component_materialize assistant-curl "$assistant_curl_cache_key" "$assistant_curl_status" \
  "$ASSISTANT_CURL_STAGE" "$RUN/components/assistant-curl"
rm -rf "$ASSISTANT_CURL_STAGE"
ASSISTANT_CURL="$RUN/components/assistant-curl/libreecho-curl"
ASSISTANT_CURL_LICENSE="$RUN/components/assistant-curl/curl-COPYING"
[[ -f "$ASSISTANT_CURL" && -f "$ASSISTANT_CURL_LICENSE" &&
   -d "$RUN/components/assistant-curl/relink" ]] || {
  echo "ERROR: assistant HTTPS runtime build is incomplete" >&2
  exit 1
}
require_relink_tree "$RUN/components/assistant-curl/relink" "assistant curl"
fi

echo "=== building static ARM32 audio probe ==="
env LD_LIBRARY_PATH="$OTA_MUSL_NATIVE_ROOT/usr/lib:$OTA_MUSL_NATIVE_ROOT/lib" \
  "$AUDIO_CC" --sysroot="$OTA_MUSL_SYSROOT" \
  -static -no-pie -Os -Wall -Wextra -Werror -Wno-cpp \
  -I"$ADBD_KERNEL_HEADERS" \
  -ffile-prefix-map="$TOOLING_SRC"=/usr/src/LibreEcho-Platform \
  -fdebug-prefix-map="$TOOLING_SRC"=/usr/src/LibreEcho-Platform \
  -fmacro-prefix-map="$TOOLING_SRC"=/usr/src/LibreEcho-Platform \
  -Wl,--build-id=none \
  -o "$RUN/audio_probe" "$TOOLS_DIR/audio_probe.c"
audio_probe_sha="$(sha256sum "$RUN/audio_probe" | awk '{print $1}')"
echo "audio_probe_sha256=$audio_probe_sha"
cp -- "$AUDIO_TOOLS_DIR/tinyplay" "$RUN/tinyplay"
cp -- "$AUDIO_TOOLS_DIR/tinycap" "$RUN/tinycap"
cp -- "$AUDIO_TOOLS_DIR/tinymix" "$RUN/tinymix"
tinyplay_sha="$(sha256sum "$RUN/tinyplay" | awk '{print $1}')"
tinycap_sha="$(sha256sum "$RUN/tinycap" | awk '{print $1}')"
tinymix_sha="$(sha256sum "$RUN/tinymix" | awk '{print $1}')"
echo "tinyplay_sha256=$tinyplay_sha"
echo "tinycap_sha256=$tinycap_sha"
echo "tinymix_sha256=$tinymix_sha"

echo "=== rebuilding or restoring static ARM32 wireless-tools iwconfig ==="
[[ -x "$NETWORK_TOOLS_BUILDER" ]] || {
  echo "ERROR: network tools builder is missing or not executable: $NETWORK_TOOLS_BUILDER" >&2
  exit 1
}
wireless_tools_cache_key="$(component_cache_key wireless-tools \
  --tree "platform-network-tools=$TOOLS_DIR/network-tools" --tree "linux-uapi=$ADBD_KERNEL_HEADERS" \
  --value "toolchain=$CORE_TOOLCHAIN_KEY" --file "source-archive=$WIRELESS_TOOLS_SOURCE_ARCHIVE")"
wireless_tools_status=rebuilt
if ! component_cache_restore wireless-tools "$wireless_tools_cache_key" "$WIRELESS_TOOLS_OUTPUT"; then
  "$NETWORK_TOOLS_BUILDER" \
    --archive "$WIRELESS_TOOLS_SOURCE_ARCHIVE" \
    --output "$WIRELESS_TOOLS_OUTPUT" \
    --cc "${MUSL_CROSS_PREFIX}gcc" \
    --ar "${MUSL_CROSS_PREFIX}ar" \
    --ranlib "${MUSL_CROSS_PREFIX}ranlib" \
    --sysroot "$OTA_MUSL_SYSROOT" \
    --native-root "$OTA_MUSL_NATIVE_ROOT" \
    --kernel-headers "$ADBD_KERNEL_HEADERS" | tee "$RUN/wireless-tools-build.log"
  component_cache_store wireless-tools "$wireless_tools_cache_key" "$WIRELESS_TOOLS_OUTPUT"
else
  wireless_tools_status=hit
  printf 'component_cache_hit=wireless-tools\n' >"$RUN/wireless-tools-build.log"
fi
component_materialize wireless-tools "$wireless_tools_cache_key" "$wireless_tools_status" "$WIRELESS_TOOLS_OUTPUT" "$RUN/components/wireless-tools"
WIRELESS_TOOLS_OUTPUT="$RUN/components/wireless-tools"
install -m 0644 "$WIRELESS_TOOLS_OUTPUT/wireless-tools-source.json" \
  "$RUN/wireless-tools-source.json"
install -m 0644 "$WIRELESS_TOOLS_OUTPUT/wireless-tools-COPYING" \
  "$RUN/wireless-tools-COPYING"
IWCONFIG_OUTPUT="$WIRELESS_TOOLS_OUTPUT/iwconfig"
[[ -f "$IWCONFIG_OUTPUT" && ! -L "$IWCONFIG_OUTPUT" ]] || {
  echo "ERROR: network tools builder did not produce iwconfig" >&2
  exit 1
}
cp -- "$IWCONFIG_OUTPUT" "$RUN/iwconfig"
iwconfig_sha="$(sha256sum "$RUN/iwconfig" | awk '{print $1}')"
echo "iwconfig_sha256=$iwconfig_sha"

echo "=== rebuilding or restoring source-locked wireless regulatory database ==="
REGDB_BUILDER="$TOOLS_DIR/network-tools/wireless-regdb/build_wireless_regdb.sh"
[[ -x "$REGDB_BUILDER" ]] || { echo "ERROR: wireless-regdb builder is missing or not executable" >&2; exit 1; }
wireless_regdb_cache_key="$(component_cache_key wireless-regdb \
  --file "source-archive=$WIRELESS_REGDB_SOURCE_ARCHIVE" --file "builder=$REGDB_BUILDER")"
wireless_regdb_status=rebuilt
if ! component_cache_restore wireless-regdb "$wireless_regdb_cache_key" "$WIRELESS_REGDB_OUTPUT"; then
  "$REGDB_BUILDER" --archive "$WIRELESS_REGDB_SOURCE_ARCHIVE" --output "$WIRELESS_REGDB_OUTPUT" | tee "$RUN/wireless-regdb-build.log"
  component_cache_store wireless-regdb "$wireless_regdb_cache_key" "$WIRELESS_REGDB_OUTPUT"
else
  wireless_regdb_status=hit
  printf 'component_cache_hit=wireless-regdb\n' >"$RUN/wireless-regdb-build.log"
fi
component_materialize wireless-regdb "$wireless_regdb_cache_key" "$wireless_regdb_status" "$WIRELESS_REGDB_OUTPUT" "$RUN/components/wireless-regdb"
WIRELESS_REGDB_OUTPUT="$RUN/components/wireless-regdb"
install -m 0644 "$WIRELESS_REGDB_OUTPUT/wireless-regdb-source.json" \
  "$RUN/wireless-regdb-source.json"
for regdb_name in regulatory.db regulatory.db.p7s; do
  generated="$WIRELESS_REGDB_OUTPUT/$regdb_name"
  overlay="$TOOLS_DIR/initramfs/$regdb_name"
  [[ -f "$generated" && ! -L "$generated" && -f "$overlay" && ! -L "$overlay" ]] || {
    echo "ERROR: wireless-regdb materialization is incomplete: $regdb_name" >&2; exit 1
  }
  [[ "$(sha256sum "$generated" | awk '{print $1}')" == "$(sha256sum "$overlay" | awk '{print $1}')" ]] || {
    echo "ERROR: wireless-regdb materialization differs from the image overlay: $regdb_name" >&2; exit 1
  }
  cp -- "$generated" "$RUN/$regdb_name"
done

AIRPLAY_PAYLOAD=
AIRPLAY_FEATURE_MANIFEST=
airplay_payload_sha=
airplay_payload_size=
airplay_feature_manifest_sha=
airplay_audio_contract_sha=
TTS_PAYLOAD=
TTS_FEATURE_MANIFEST=
tts_payload_sha=
tts_payload_size=
tts_feature_manifest_sha=
WAKE_PAYLOAD=
WAKE_FEATURE_MANIFEST=
wake_payload_sha=
wake_payload_size=
wake_feature_manifest_sha=
STT_PAYLOAD=
STT_FEATURE_MANIFEST=
stt_payload_sha=
stt_payload_size=
stt_feature_manifest_sha=
ASSISTANT_PAYLOAD=
ASSISTANT_FEATURE_MANIFEST=
assistant_payload_sha=
assistant_payload_size=
assistant_feature_manifest_sha=
feature_builder_args=()
feature_verifier_args=()
if [[ "$FEATURES_ENABLED" == 1 ]]; then
echo "=== validating immutable Radar-Puffin speaker transport contract ==="
AIRPLAY_AUDIO_CONTRACT="$TOOLS_DIR/airplay/test_audio_engine_contract.py"
[[ -f "$AIRPLAY_AUDIO_CONTRACT" && ! -L "$AIRPLAY_AUDIO_CONTRACT" ]] || {
  echo "ERROR: AirPlay audio transport contract is missing: $AIRPLAY_AUDIO_CONTRACT" >&2
  exit 1
}
python3 -B "$AIRPLAY_AUDIO_CONTRACT" | tee "$RUN/airplay-audio-contract.log"
airplay_audio_contract_sha="$(sha256sum "$RUN/airplay-audio-contract.log" | awk '{print $1}')"

echo "=== building packaged ARM32 AirPlay 2 payload ==="
AIRPLAY_BUILDER="$TOOLS_DIR/airplay/build_airplay.sh"
[[ -x "$AIRPLAY_BUILDER" ]] || {
  echo "ERROR: AirPlay builder is missing or not executable: $AIRPLAY_BUILDER" >&2
  exit 1
}
[[ -d "$AIRPLAY_SYSROOT" ]] || {
  echo "ERROR: missing pinned ARMHF AirPlay dependency sysroot: $AIRPLAY_SYSROOT" >&2
  echo "       Set LIBREECHO_AIRPLAY_SYSROOT to the reviewed sysroot containing Avahi, ALSA, OpenSSL, libplist, libsodium, libgcrypt, UUID and the glibc runtime closure." >&2
  exit 1
}
[[ -x "$AIRPLAY_CXX" ]] || {
  echo "ERROR: ARMHF C++ compiler not found: $AIRPLAY_CXX" >&2
  exit 1
}
airplay_build_env=("CXX=$AIRPLAY_CXX")
if [[ -n "$AIRPLAY_COMPILER_PATH" ]]; then
  airplay_build_env+=("COMPILER_PATH=$AIRPLAY_COMPILER_PATH")
fi
if [[ -n "$AIRPLAY_HOST_BIN" ]]; then
  airplay_build_env+=("PATH=$AIRPLAY_HOST_BIN:$PATH")
fi
airplay_build_env+=("LD_LIBRARY_PATH=$AIRPLAY_HOST_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}")
airplay_build_env+=("LIBREECHO_AIRPLAY_ALSA_DATA=$AIRPLAY_ALSA_DATA")

# The 3.18 TinyALSA build needs the legacy kernel UAPI.  In 6.1,
# include/linux/compiler_types.h makes that injection collide with the
# userspace __user compatibility macro in the pinned TinyALSA sources; the
# cross-toolchain headers are the correct userspace interface there.
if [[ ! -f "$KERNEL_SRC/include/linux/compiler_types.h" ]]; then
  airplay_build_env+=("LIBREECHO_AIRPLAY_KERNEL_HEADERS=$KERNEL_SRC")
fi
# AirPlay is cached as a complete, symlink-free builder output.  The key
# includes the Platform AirPlay subtree, all pinned archives, the target sysroot,
# host plistutil, compiler, and ALSA data, so a changed AirPlay fix invalidates
# only AirPlay rather than every core component.
airplay_cache_key_args=(
  --value "payload_layout=v2" \
  --tree "platform-airplay=$TOOLS_DIR/airplay" \
  --value "airplay-sysroot=$AIRPLAY_SYSROOT_KEY" --tree "alsa-data=$AIRPLAY_ALSA_DATA" \
  --file "nqptp-source=$INPUTS/nqptp-1.2.8.tar.gz" \
  --file "shairport-source=$INPUTS/shairport-sync-5.1.tar.gz" \
  --file "ffmpeg-source=$INPUTS/ffmpeg-6.1.1.tar.xz" \
  --file "tinyalsa-source=$AIRPLAY_TINYALSA_ARCHIVE" \
  --file "airplay-cxx=$AIRPLAY_CXX" --file "host-plistutil=$AIRPLAY_PLISTUTIL" \
  --tree "host-lib=$AIRPLAY_HOST_LIB" \
  --value "compiler_path_configured=${AIRPLAY_COMPILER_PATH:+1}"
)
if [[ -n "$AIRPLAY_HOST_BIN" ]]; then
  airplay_cache_key_args+=(--tree "host-bin=$AIRPLAY_HOST_BIN")
fi
if [[ -n "$AIRPLAY_COMPILER_PATH" ]]; then
  IFS=: read -r -a airplay_compiler_paths <<<"$AIRPLAY_COMPILER_PATH"
  for index in "${!airplay_compiler_paths[@]}"; do
    compiler_path=${airplay_compiler_paths[$index]}
    [[ -d "$compiler_path" && ! -L "$compiler_path" ]] || {
      echo "ERROR: unsafe AirPlay COMPILER_PATH entry: $compiler_path" >&2
      exit 1
    }
    airplay_cache_key_args+=(--tree "compiler-path-$index=$compiler_path")
  done
fi
if [[ ! -f "$KERNEL_SRC/include/linux/compiler_types.h" ]]; then
  airplay_cache_key_args+=(--tree "airplay-kernel-headers=$KERNEL_SRC")
else
  airplay_cache_key_args+=(--value "airplay_kernel_headers=toolchain-userspace")
fi
airplay_cache_key="$(component_cache_key airplay "${airplay_cache_key_args[@]}")"
AIRPLAY_STAGE="$RUN/airplay-stage"
airplay_status=rebuilt
rm -rf "$AIRPLAY_STAGE" "$RUN/components/airplay"
# The cache payload carries both the builder output and the relink object
# tree.  On a cache hit the builder never runs, so the relink objects needed
# by the corresponding-source offer must come from the same verified payload.
if ! component_cache_restore airplay "$airplay_cache_key" "$AIRPLAY_STAGE"; then
  mkdir -p "$AIRPLAY_STAGE"
  env "${airplay_build_env[@]}" \
    "LIBREECHO_AIRPLAY_RELINK_OUTPUT=$AIRPLAY_STAGE/airplay-relink" \
    "$AIRPLAY_BUILDER" \
    "$INPUTS/nqptp-1.2.8.tar.gz" \
    "$INPUTS/shairport-sync-5.1.tar.gz" \
    "$INPUTS/ffmpeg-6.1.1.tar.xz" \
    "$AIRPLAY_TINYALSA_ARCHIVE" \
    "$AIRPLAY_SYSROOT" "$AIRPLAY_STAGE/airplay" | tee "$RUN/airplay-build.log"
  require_relink_tree "$AIRPLAY_STAGE/airplay-relink" "AirPlay"
  component_cache_store airplay "$airplay_cache_key" "$AIRPLAY_STAGE"
else
  airplay_status=hit
  printf 'component_cache_hit=airplay\n' >"$RUN/airplay-build.log"
fi
[[ -d "$AIRPLAY_STAGE/airplay" && -d "$AIRPLAY_STAGE/airplay-relink" ]] || {
  echo "ERROR: AirPlay cache payload is incomplete (airplay or airplay-relink missing)" >&2
  exit 1
}
require_relink_tree "$AIRPLAY_STAGE/airplay-relink" "AirPlay"
component_materialize airplay "$airplay_cache_key" "$airplay_status" \
  "$AIRPLAY_STAGE" "$RUN/components/airplay"
rm -rf "$AIRPLAY_STAGE"
AIRPLAY_OUTPUT="$RUN/components/airplay/airplay"
[[ -f "$AIRPLAY_OUTPUT/nqptp" && -f "$AIRPLAY_OUTPUT/shairport-sync" && \
   -f "$AIRPLAY_OUTPUT/avahi-daemon" && -f "$AIRPLAY_OUTPUT/dbus-daemon" && \
   -f "$AIRPLAY_OUTPUT/libreecho-airplay-audio" && \
   -f "$AIRPLAY_OUTPUT/libreecho-audio-engine" && \
   -d "$AIRPLAY_OUTPUT/runtime" ]] || {
  echo "ERROR: AirPlay builder did not produce the complete payload" >&2
  exit 1
}
nqptp_sha="$(sha256sum "$AIRPLAY_OUTPUT/nqptp" | awk '{print $1}')"
shairport_sync_sha="$(sha256sum "$AIRPLAY_OUTPUT/shairport-sync" | awk '{print $1}')"
avahi_daemon_sha="$(sha256sum "$AIRPLAY_OUTPUT/avahi-daemon" | awk '{print $1}')"
dbus_daemon_sha="$(sha256sum "$AIRPLAY_OUTPUT/dbus-daemon" | awk '{print $1}')"
audio_producer_sha="$(sha256sum "$AIRPLAY_OUTPUT/libreecho-airplay-audio" | awk '{print $1}')"
audio_engine_sha="$(sha256sum "$AIRPLAY_OUTPUT/libreecho-audio-engine" | awk '{print $1}')"
echo "nqptp_sha256=$nqptp_sha"
echo "shairport_sync_sha256=$shairport_sync_sha"
echo "avahi_daemon_sha256=$avahi_daemon_sha"
echo "dbus_daemon_sha256=$dbus_daemon_sha"
echo "audio_producer_sha256=$audio_producer_sha"
echo "audio_engine_sha256=$audio_engine_sha"

echo "=== packaging AirPlay 2 as an external feature payload ==="
AIRPLAY_FEATURE_PACKAGER="$TOOLS_DIR/airplay/package_feature.sh"
[[ -x "$AIRPLAY_FEATURE_PACKAGER" ]] || {
  echo "ERROR: AirPlay feature packager is missing or not executable: $AIRPLAY_FEATURE_PACKAGER" >&2
  exit 1
}
AIRPLAY_PAYLOAD="$RUN/features/airplay2.squashfs"
AIRPLAY_FEATURE_MANIFEST="$RUN/features/airplay2.manifest.json"
mkdir -p "$RUN/features"
if [[ -n "$AIRPLAY_PAYLOAD_OVERRIDE" || -n "$AIRPLAY_MANIFEST_OVERRIDE" ]]; then
  adopt_feature_payload airplay2 "$AIRPLAY_PAYLOAD_OVERRIDE" "$AIRPLAY_MANIFEST_OVERRIDE" \
    "$AIRPLAY_PAYLOAD" "$AIRPLAY_FEATURE_MANIFEST" | tee "$RUN/airplay-feature-build.log"
else
  "$AIRPLAY_FEATURE_PACKAGER" "$AIRPLAY_OUTPUT" "$UI_BUNDLE" \
    "$AIRPLAY_PAYLOAD" "$AIRPLAY_FEATURE_MANIFEST" | tee "$RUN/airplay-feature-build.log"
fi
[[ -f "$AIRPLAY_PAYLOAD" && -f "$AIRPLAY_FEATURE_MANIFEST" ]] || {
  echo "ERROR: AirPlay feature payload package is incomplete" >&2
  exit 1
}
airplay_payload_sha="$(sha256sum "$AIRPLAY_PAYLOAD" | awk '{print $1}')"
airplay_payload_size="$(stat -c %s "$AIRPLAY_PAYLOAD")"
airplay_feature_manifest_sha="$(sha256sum "$AIRPLAY_FEATURE_MANIFEST" | awk '{print $1}')"
echo "airplay_payload_sha256=$airplay_payload_sha"
echo "airplay_payload_size=$airplay_payload_size"
echo "airplay_feature_manifest_sha256=$airplay_feature_manifest_sha"

echo "=== packaging two-voice TTS as an external feature payload ==="
TTS_FEATURE_PACKAGER="$TOOLS_DIR/tts/package_feature.sh"
[[ -x "$TTS_FEATURE_PACKAGER" ]] || {
  echo "ERROR: TTS feature packager is missing or not executable: $TTS_FEATURE_PACKAGER" >&2
  exit 1
}
TTS_PAYLOAD="$RUN/features/tts.squashfs"
TTS_FEATURE_MANIFEST="$RUN/features/tts.manifest.json"
"$TTS_FEATURE_PACKAGER" "$TTS_DAEMON" "$TTS_NORTHERN_MALE_MODEL" \
  "$TTS_FEMALE_MODEL" "$TTS_TOKENS" "$TTS_ESPEAK_DATA" \
  "$TTS_PAYLOAD" "$TTS_FEATURE_MANIFEST" | tee "$RUN/tts-feature-build.log"
[[ -f "$TTS_PAYLOAD" && -f "$TTS_FEATURE_MANIFEST" ]] || {
  echo "ERROR: TTS feature payload package is incomplete" >&2
  exit 1
}
tts_payload_sha="$(sha256sum "$TTS_PAYLOAD" | awk '{print $1}')"
tts_payload_size="$(stat -c %s "$TTS_PAYLOAD")"
tts_feature_manifest_sha="$(sha256sum "$TTS_FEATURE_MANIFEST" | awk '{print $1}')"
echo "tts_payload_sha256=$tts_payload_sha"
echo "tts_payload_size=$tts_payload_size"
echo "tts_feature_manifest_sha256=$tts_feature_manifest_sha"

if [[ "$WAKEWORD_ENABLED" == 1 ]]; then
  echo "=== packaging openWakeWord as an external feature payload ==="
WAKE_FEATURE_PACKAGER="$TOOLS_DIR/wakeword/package_feature.sh"
[[ -x "$WAKE_FEATURE_PACKAGER" ]] || {
  echo "ERROR: wakeword feature packager is missing: $WAKE_FEATURE_PACKAGER" >&2
  exit 1
}
WAKE_PAYLOAD="$RUN/features/wakeword.squashfs"
WAKE_FEATURE_MANIFEST="$RUN/features/wakeword.manifest.json"
"$WAKE_FEATURE_PACKAGER" "$WAKE_DAEMON" "$WAKE_MEL_MODEL" \
  "$WAKE_EMBEDDING_MODEL" "$WAKE_CLASSIFIER_MODEL" \
  "$WAKE_PAYLOAD" "$WAKE_FEATURE_MANIFEST" | tee "$RUN/wakeword-feature-build.log"
[[ -f "$WAKE_PAYLOAD" && -f "$WAKE_FEATURE_MANIFEST" ]] || {
  echo "ERROR: wakeword feature payload package is incomplete" >&2
  exit 1
}
wake_payload_sha="$(sha256sum "$WAKE_PAYLOAD" | awk '{print $1}')"
wake_payload_size="$(stat -c %s "$WAKE_PAYLOAD")"
wake_feature_manifest_sha="$(sha256sum "$WAKE_FEATURE_MANIFEST" | awk '{print $1}')"
echo "wakeword_payload_sha256=$wake_payload_sha"
echo "wakeword_payload_size=$wake_payload_size"
echo "wakeword_feature_manifest_sha256=$wake_feature_manifest_sha"
fi

echo "=== packaging English streaming STT as an external feature payload ==="
STT_FEATURE_PACKAGER="$TOOLS_DIR/stt/package_feature.sh"
[[ -x "$STT_FEATURE_PACKAGER" ]] || {
  echo "ERROR: STT feature packager is missing: $STT_FEATURE_PACKAGER" >&2
  exit 1
}
STT_PAYLOAD="$RUN/features/stt.squashfs"
STT_FEATURE_MANIFEST="$RUN/features/stt.manifest.json"
"$STT_FEATURE_PACKAGER" "$STT_DAEMON" "$STT_ENCODER" "$STT_DECODER" \
  "$STT_JOINER" "$STT_TOKENS" "$STT_MODEL_LICENSE" \
  "$STT_PAYLOAD" "$STT_FEATURE_MANIFEST" | tee "$RUN/stt-feature-build.log"
[[ -f "$STT_PAYLOAD" && -f "$STT_FEATURE_MANIFEST" ]] || {
  echo "ERROR: STT feature payload package is incomplete" >&2
  exit 1
}
stt_payload_sha="$(sha256sum "$STT_PAYLOAD" | awk '{print $1}')"
stt_payload_size="$(stat -c %s "$STT_PAYLOAD")"
stt_feature_manifest_sha="$(sha256sum "$STT_FEATURE_MANIFEST" | awk '{print $1}')"
echo "stt_payload_sha256=$stt_payload_sha"
echo "stt_payload_size=$stt_payload_size"
echo "stt_feature_manifest_sha256=$stt_feature_manifest_sha"

echo "=== packaging streamed assistant as an external feature payload ==="
ASSISTANT_FEATURE_PACKAGER="$TOOLS_DIR/assistant/package_feature.sh"
[[ -x "$ASSISTANT_FEATURE_PACKAGER" ]] || {
  echo "ERROR: assistant feature packager is missing: $ASSISTANT_FEATURE_PACKAGER" >&2
  exit 1
}
ASSISTANT_PAYLOAD="$RUN/features/assistant.squashfs"
ASSISTANT_FEATURE_MANIFEST="$RUN/features/assistant.manifest.json"
if [[ -n "$ASSISTANT_PAYLOAD_OVERRIDE" || -n "$ASSISTANT_MANIFEST_OVERRIDE" ]]; then
  adopt_feature_payload assistant "$ASSISTANT_PAYLOAD_OVERRIDE" "$ASSISTANT_MANIFEST_OVERRIDE" \
    "$ASSISTANT_PAYLOAD" "$ASSISTANT_FEATURE_MANIFEST" | tee "$RUN/assistant-feature-build.log"
else
  "$ASSISTANT_FEATURE_PACKAGER" "$AGENT_DAEMON" "$ASSISTANT_CURL" \
    "$ASSISTANT_CA_BUNDLE" "$ASSISTANT_CURL_LICENSE" \
    "$ASSISTANT_CA_COPYRIGHT" "$ASSISTANT_PAYLOAD" \
    "$ASSISTANT_FEATURE_MANIFEST" | tee "$RUN/assistant-feature-build.log"
fi
[[ -f "$ASSISTANT_PAYLOAD" && -f "$ASSISTANT_FEATURE_MANIFEST" ]] || {
  echo "ERROR: assistant feature payload package is incomplete" >&2
  exit 1
}
assistant_payload_sha="$(sha256sum "$ASSISTANT_PAYLOAD" | awk '{print $1}')"
assistant_payload_size="$(stat -c %s "$ASSISTANT_PAYLOAD")"
assistant_feature_manifest_sha="$(
  sha256sum "$ASSISTANT_FEATURE_MANIFEST" | awk '{print $1}'
)"
echo "assistant_payload_sha256=$assistant_payload_sha"
echo "assistant_payload_size=$assistant_payload_size"
echo "assistant_feature_manifest_sha256=$assistant_feature_manifest_sha"
feature_builder_args=(
  --airplay-payload "$AIRPLAY_PAYLOAD"
  --airplay-payload-manifest "$AIRPLAY_FEATURE_MANIFEST"
  --tts-payload "$TTS_PAYLOAD"
  --tts-payload-manifest "$TTS_FEATURE_MANIFEST"
  --stt-payload "$STT_PAYLOAD"
  --stt-payload-manifest "$STT_FEATURE_MANIFEST"
  --assistant-payload "$ASSISTANT_PAYLOAD"
  --assistant-payload-manifest "$ASSISTANT_FEATURE_MANIFEST"
)
feature_verifier_args=(
  --expected-airplay-payload-sha256 "$airplay_payload_sha"
  --expected-airplay-payload-size "$airplay_payload_size"
  --expected-tts-payload-sha256 "$tts_payload_sha"
  --expected-tts-payload-size "$tts_payload_size"
  --expected-stt-payload-sha256 "$stt_payload_sha"
  --expected-stt-payload-size "$stt_payload_size"
  --expected-assistant-payload-sha256 "$assistant_payload_sha"
  --expected-assistant-payload-size "$assistant_payload_size"
)
if [[ "$WAKEWORD_ENABLED" == 1 ]]; then
  feature_builder_args+=(
    --wakeword-payload "$WAKE_PAYLOAD"
    --wakeword-payload-manifest "$WAKE_FEATURE_MANIFEST"
  )
  feature_verifier_args+=(
    --expected-wakeword-payload-sha256 "$wake_payload_sha"
    --expected-wakeword-payload-size "$wake_payload_size"
  )
fi
fi

ssh_builder_args=()
ssh_verifier_args=()
dropbear_sha=
dropbearkey_sha=
if [[ "$SSH_ENABLED" == 1 ]]; then
  echo "=== building static ARM32 password-only SSH server ==="
  DROPBEAR_BUILDER="$TOOLS_DIR/ssh/build_dropbear.sh"
  [[ -x "$DROPBEAR_BUILDER" ]] || {
    echo "ERROR: SSH builder is missing or not executable: $DROPBEAR_BUILDER" >&2
    exit 1
  }
  LIBREECHO_PIPELINE_ROOT="$BUILD_ROOT" \
    "$DROPBEAR_BUILDER" | tee "$RUN/dropbear-build.log"
  DROPBEAR_OUTPUT="$WORK_ROOT/dropbear-2026.93/output"
  [[ -f "$DROPBEAR_OUTPUT/dropbear" && -f "$DROPBEAR_OUTPUT/dropbearkey" ]] || {
    echo "ERROR: SSH builder did not produce both Dropbear binaries" >&2
    exit 1
  }
  dropbear_sha="$(sha256sum "$DROPBEAR_OUTPUT/dropbear" | awk '{print $1}')"
  dropbearkey_sha="$(sha256sum "$DROPBEAR_OUTPUT/dropbearkey" | awk '{print $1}')"
  echo "dropbear_sha256=$dropbear_sha"
  echo "dropbearkey_sha256=$dropbearkey_sha"
  ssh_builder_args=(
    --ssh-enabled
    --dropbear "$DROPBEAR_OUTPUT/dropbear"
    --dropbearkey "$DROPBEAR_OUTPUT/dropbearkey"
    --ssh-root-password-hash "$SSH_ROOT_PASSWORD_HASH"
  )
  ssh_verifier_args=(
    --expected-dropbear-sha256 "$dropbear_sha"
    --expected-dropbearkey-sha256 "$dropbearkey_sha"
  )
fi

echo "=== checking kernel marker contract ==="
"$PIPELINE/check_marker_contract.sh" "$RUN/System.map" "$IMAGE_PROFILE" \
  "$KERNEL_SRC" "$TOOLING_SRC"

BUILDER="$TOOLS_DIR/build_recovery_image.py"
VERIFIER="$TOOLS_DIR/verify_recovery_image.py"
IMAGE_DTB="$RUN/libreecho-radar-puffin.dtb"
IMAGE_DTB_SHA256="$dtbsha"
echo "radar_puffin_dtb_sha256=$IMAGE_DTB_SHA256"
echo "=== packaging canonical ARM32 recovery image ==="
BOOT_ENVELOPE="$RUN/boot-envelope.bin"
python3 -B "$BOOT_ENVELOPE_GENERATOR" --output "$BOOT_ENVELOPE" | tee "$RUN/boot-envelope-build.log"
python3 -B "$BUILDER" \
  --boot-envelope "$BOOT_ENVELOPE" \
  --adbd "$ADBD_BINARY" --adbd-source-metadata "$ADBD_METADATA" \
  --busybox "$BUSYBOX_OUTPUT/busybox" --expected-busybox-sha256 "$busybox_sha" \
  --musl-loader "$MUSL_LOADER" --expected-musl-loader-sha256 "$musl_loader_sha" \
  --image-profile "$IMAGE_PROFILE" --service-profile "$SERVICE_PROFILE" \
  --update-channel "$UPDATE_CHANNEL" \
  --feature-policy "$FEATURE_POLICY" \
  --bootctl "$OTA_BOOTCTL" \
  --update-verifier "$OTA_VERIFIER" --ota-public-key "$OTA_PUBLIC_KEY" \
  --audio-probe "$RUN/audio_probe" \
  --tinyplay "$RUN/tinyplay" --tinycap "$RUN/tinycap" --tinymix "$RUN/tinymix" \
  --iwconfig "$RUN/iwconfig" \
  --iwconfig-source-metadata "$RUN/wireless-tools-source.json" \
  --ui-bundle "$UI_BUNDLE" --ui-source "$UI_SOURCE" \
  --expected-ui-commit "$ui_commit" --expected-ui-diff-sha256 "$ui_diff_sha" \
  "${feature_builder_args[@]}" \
  --wmt-config-helper "$CONNECTIVITY_HELPERS/wmt_configure" \
  --wmt-responder "$CONNECTIVITY_HELPERS/wmt_responder" \
  --wmt-bt-on "$CONNECTIVITY_HELPERS/wmt_bt_on" \
  --wmt-stock-compat "$CONNECTIVITY_HELPERS/wmt_stock_compat" \
  --wmt-launcher "$CONNECTIVITY_HELPERS/wmt_launcher" \
  --wpa-supplicant "$WPA_SUPPLICANT" \
  --wpa-source-metadata "$RUN/wpa-supplicant-source.json" \
  --wifi-config "$IMAGE_WIFI_CONFIG" \
  "${ssh_builder_args[@]}" \
  --zimage "$RUN/zImage" --expected-zimage-sha256 "$zsha" \
  --system-map "$RUN/System.map" --expected-system-map-sha256 "$mapsha" \
  --dtb "$IMAGE_DTB" --expected-dtb-sha256 "$IMAGE_DTB_SHA256" \
  --output "$RUN/boot.img" \
  --ramdisk-output "$RUN/boot.ramdisk.cpio.gz" \
  --manifest "$RUN/manifest.json" | tee "$RUN/build.log"

bootsha="$(sha256sum "$RUN/boot.img" | awk '{print $1}')"
echo "=== independent image verification ==="
python3 -B "$VERIFIER" \
  --boot-envelope "$BOOT_ENVELOPE" \
  --zimage "$RUN/zImage" --expected-zimage-sha256 "$zsha" \
  --system-map "$RUN/System.map" --expected-system-map-sha256 "$mapsha" \
  --ramdisk "$RUN/boot.ramdisk.cpio.gz" --manifest "$RUN/manifest.json" \
  --boot-image "$RUN/boot.img" --expected-boot-sha256 "$bootsha" \
  --expected-busybox-sha256 "$busybox_sha" \
  --expected-musl-loader-sha256 "$musl_loader_sha" \
  --expected-adbd-sha256 "$adbd_sha" \
  --expected-dtb-sha256 "$IMAGE_DTB_SHA256" \
  --expected-audio-probe-sha256 "$audio_probe_sha" \
  --expected-tinyplay-sha256 "$tinyplay_sha" \
  --expected-tinycap-sha256 "$tinycap_sha" \
  --expected-tinymix-sha256 "$tinymix_sha" \
  "${feature_verifier_args[@]}" \
  --expected-iwconfig-sha256 "$iwconfig_sha" \
  --expected-image-profile "$IMAGE_PROFILE" \
  --expected-service-profile "$SERVICE_PROFILE" \
  --expected-feature-policy "$FEATURE_POLICY" \
  --expected-update-channel "$UPDATE_CHANNEL" \
  --expected-bootctl-sha256 "$ota_bootctl_sha" \
  --expected-update-verifier-sha256 "$ota_verifier_sha" \
  --expected-ota-public-key-sha256 "$ota_public_key_sha" \
  --expected-ui-manifest-sha256 "$ui_manifest_sha" \
  --expected-ui-commit "$ui_commit" --expected-ui-diff-sha256 "$ui_diff_sha" \
  "${ssh_verifier_args[@]}" \
  --expected-connectivity-bundle mt8163-v181-stock-v1 | tee "$RUN/verify.log"

USERDATA_TREE=
userdata_manifest_sha=
userdata_tree_bytes=
if [[ "$FEATURES_ENABLED" == 1 && "$PUBLIC_RELEASE_MODE" != 1 ]]; then
  USERDATA_TREE="$RUN/userdata-tree"
  echo "=== preparing clean production userdata tree ==="
  "$PIPELINE/prepare_userdata_tree.sh" "$RUN" "$USERDATA_TREE" | tee "$RUN/userdata-tree-build.log"
  userdata_manifest_sha="$(sha256sum "$USERDATA_TREE/libreecho/data-manifest.json" | awk '{print $1}')"
  userdata_tree_bytes="$(du -s -B1 "$USERDATA_TREE" | awk '{print $1}')"
  echo "userdata_tree_manifest_sha256=$userdata_manifest_sha"
  echo "userdata_tree_bytes=$userdata_tree_bytes"
else
  echo "=== feature userdata excluded by public base policy ==="
fi

source_offer_index=
source_offer_manifest_sha256=
source_offer_archives=()
if [[ "$FEATURES_ENABLED" == 1 && "$PUBLIC_RELEASE_MODE" != 1 ]]; then
  echo "=== snapshotting immutable run-local source-offer relink inputs ==="
  SOURCE_OFFER_RELINK_STAGE="$RUN/source-offer-relink-stage"
  SOURCE_OFFER_RELINK_ROOT="$RUN/components/source-offer-relink"
  rm -rf "$SOURCE_OFFER_RELINK_STAGE" "$SOURCE_OFFER_RELINK_ROOT"
  mkdir -p "$SOURCE_OFFER_RELINK_STAGE"
  snapshot_relink_root() {
    local source=$1 label=$2 destination="$SOURCE_OFFER_RELINK_STAGE/$2"
    local file relative count=0
    [[ -d "$source" && ! -L "$source" ]] || {
      echo "ERROR: unsafe relink snapshot source: $source" >&2
      exit 1
    }
    mkdir -p "$destination"
    while IFS= read -r -d '' file; do
      [[ ! -L "$file" ]] || {
        echo "ERROR: relink snapshot input is a symlink: $file" >&2
        exit 1
      }
      relative=${file#"$source"/}
      mkdir -p "$destination/$(dirname -- "$relative")"
      cp --preserve=mode,timestamps -- "$file" "$destination/$relative"
      count=$((count + 1))
    done < <(find "$source" -type f \( -name '*.o' -o -name '*.a' \) -print0 | LC_ALL=C sort -z)
    ((count > 0)) || {
      echo "ERROR: relink snapshot source contains no objects: $source" >&2
      exit 1
    }
  }
  snapshot_relink_root "$SHERPA_PREFIX" sherpa
  snapshot_relink_root "$ORT_PREFIX" onnxruntime
  snapshot_relink_root "$ORT_BUILD" onnxruntime-build
  snapshot_relink_root "$FLITE_SOURCE/build/arm-linux-gnueabihf" \
    flite-root/build/arm-linux-gnueabihf
  snapshot_relink_root "$SPEEX_PREFIX" speexdsp
  snapshot_relink_root "$CORE_RUNTIME_SYSROOT/lib" core-runtime-root/lib
  snapshot_relink_root "$CORE_GCC_LIBDIR" gcc-runtime
  source_offer_relink_key="$(component_cache_key source-offer-relink \
    --value "contract=complete-relink-snapshot-v1" \
    --tree "source-offer-relink=$SOURCE_OFFER_RELINK_STAGE")"
  component_materialize source-offer-relink "$source_offer_relink_key" rebuilt \
    "$SOURCE_OFFER_RELINK_STAGE" "$SOURCE_OFFER_RELINK_ROOT"
  rm -rf "$SOURCE_OFFER_RELINK_STAGE"

  echo "=== assembling corresponding-source and relink offers ==="
  "$ASSEMBLE_SOURCE_OFFERS" "$RUN" "$TOOLING_SRC" "$UI_SOURCE" \
    "$INPUTS" "$SOURCE_OFFER_INPUTS" "$SOURCE_OFFER_RELINK_ROOT/sherpa" \
    "$SOURCE_OFFER_RELINK_ROOT/onnxruntime" \
    "$SOURCE_OFFER_RELINK_ROOT/onnxruntime-build" \
    "$SOURCE_OFFER_RELINK_ROOT/flite-root" "$SOURCE_OFFER_RELINK_ROOT/speexdsp" \
    "$WAKE_ORT_BUILD" "$WAKE_SPEEX_PREFIX" \
    "$SOURCE_OFFER_RELINK_ROOT/core-runtime-root" \
    "$SOURCE_OFFER_RELINK_ROOT/gcc-runtime" \
    "$FEATURE_POLICY" \
    | tee "$RUN/source-offer-build.log"
  source_offer_index="$RUN/source-offers/source-offer-index.json"
  source_offer_manifest_sha256="$(sha256sum "$source_offer_index" | awk '{print $1}')"
  source_offer_archives=(
    "$RUN/source-offers/core-runtime-closure.source-offer.tar.gz"
    "$RUN/source-offers/airplay-payload.source-offer.tar.gz"
    "$RUN/source-offers/stt-payload.source-offer.tar.gz"
    "$RUN/source-offers/tts-payload.source-offer.tar.gz"
    "$RUN/source-offers/assistant-payload.source-offer.tar.gz"
  )
  if [[ "$WAKEWORD_ENABLED" == 1 ]]; then
    source_offer_archives+=("$RUN/source-offers/wakeword-payload.source-offer.tar.gz")
  fi
  for source_offer in "${source_offer_archives[@]}"; do
    [[ -f "$source_offer" && ! -L "$source_offer" ]] || {
      echo "ERROR: source-offer assembly is incomplete: $source_offer" >&2
      exit 1
    }
  done
  echo "source_offer_manifest_sha256=$source_offer_manifest_sha256"
fi

ota_bundle=
ota_bundle_sha=
if [[ "$IMAGE_PROFILE" == ota && "$OTA_SIGNING_MODE" == local ]]; then
  echo "=== creating signed LibreEcho OTA bundle ==="
  ota_bundle="$RUN/libreecho-${run_id}.ota.tar"
  python3 -B "$OTA_DIR/make_ota_bundle.py" \
    --boot-image "$RUN/boot.img" --build-manifest "$RUN/manifest.json" \
    --version "$run_id" \
    --signing-key "$OTA_SIGNING_KEY" --public-key "$OTA_PUBLIC_KEY" \
    --service-profile "$SERVICE_PROFILE" --feature-policy "$FEATURE_POLICY" \
    --update-channel "$UPDATE_CHANNEL" \
    --output "$ota_bundle" | tee "$RUN/ota-bundle.log"
  ota_bundle_sha="$(sha256sum "$ota_bundle" | awk '{print $1}')"
fi

[[ -f "$COMPONENTS_MANIFEST" && ! -L "$COMPONENTS_MANIFEST" ]] || {
  echo "ERROR: run-local component manifest is missing" >&2
  exit 1
}
cp -- "$COMPONENT_TIMING_FILE" "$RUN/component-timing.log"
cp -- "$COMPONENT_IDENTITY_FILE" "$RUN/component-identities.log"
component_timing_sha="$(sha256sum "$RUN/component-timing.log" | awk '{print $1}')"
component_identities_sha="$(sha256sum "$RUN/component-identities.log" | awk '{print $1}')"
components_manifest_sha="$(sha256sum "$COMPONENTS_MANIFEST" | awk '{print $1}')"

cat > "$RUN/provenance.txt" <<EOF
schema=1
status=PREPARED_NOT_FLASHED
image_profile=$IMAGE_PROFILE
service_profile=$SERVICE_PROFILE
feature_policy=$FEATURE_POLICY
update_channel=$UPDATE_CHANNEL
run_id=$run_id
public_release_mode=$PUBLIC_RELEASE_MODE
build_source=$PIPELINE
build_git_head=$build_head
build_git_state=$build_state
build_git_diff_sha256=$build_diffsha
product_source=$PRODUCT_SRC
product_git_head=$product_head
product_git_state=$product_state
product_git_diff_sha256=$product_diffsha
kernel_source=$KERNEL_SRC
tooling_source=$TOOLING_SRC
tooling_git_head=$tooling_head
tooling_git_state=$tooling_state
tooling_git_diff_sha256=$tooling_diffsha
kernel_git_head=$head
kernel_git_state=$dirty
kernel_git_diff_sha256=$kernel_diffsha
kernel_config=$RUN/kernel.config
kernel_config_sha256=$kernel_config_sha
expected_dtb_sha256=$IMAGE_DTB_SHA256
zimage=$RUN/zImage
zimage_sha256=$zsha
system_map=$RUN/System.map
system_map_sha256=$mapsha
boot_image=$RUN/boot.img
boot_image_sha256=$bootsha
boot_envelope=$BOOT_ENVELOPE
boot_envelope_sha256=$(sha256sum "$BOOT_ENVELOPE" | awk '{print $1}')
components_manifest=$COMPONENTS_MANIFEST
components_manifest_sha256=$components_manifest_sha
component_timing=$RUN/component-timing.log
component_timing_sha256=$component_timing_sha
component_identities=$RUN/component-identities.log
component_identities_sha256=$component_identities_sha
busybox=$BUSYBOX_OUTPUT/busybox
busybox_sha256=$busybox_sha
busybox_source_metadata=$RUN/busybox-source.json
musl_loader=$MUSL_LOADER
musl_loader_sha256=$musl_loader_sha
musl_source_metadata=$RUN/musl-source.json
wpa_supplicant=$WPA_SUPPLICANT
wpa_supplicant_sha256=$wpa_supplicant_sha
wpa_supplicant_source_metadata=$RUN/wpa-supplicant-source.json
connectivity_source_metadata=$RUN/connectivity-source.json
tinyalsa_source_metadata=$RUN/tinyalsa-source.json
wireless_tools_source_metadata=$RUN/wireless-tools-source.json
wireless_regdb_source_metadata=$RUN/wireless-regdb-source.json
libsodium_source_metadata=$RUN/libsodium-source.json
audio_probe=$RUN/audio_probe
audio_probe_sha256=$audio_probe_sha
tinyplay=$RUN/tinyplay
tinyplay_sha256=$tinyplay_sha
tinycap=$RUN/tinycap
tinycap_sha256=$tinycap_sha
tinymix=$RUN/tinymix
tinymix_sha256=$tinymix_sha
iwconfig=$RUN/iwconfig
iwconfig_sha256=$iwconfig_sha
airplay_payload=$AIRPLAY_PAYLOAD
airplay_payload_sha256=$airplay_payload_sha
airplay_payload_size=$airplay_payload_size
airplay_feature_manifest=$AIRPLAY_FEATURE_MANIFEST
airplay_feature_manifest_sha256=$airplay_feature_manifest_sha
airplay_audio_contract=$RUN/airplay-audio-contract.log
airplay_audio_contract_sha256=$airplay_audio_contract_sha
tts_payload=$TTS_PAYLOAD
tts_payload_sha256=$tts_payload_sha
tts_payload_size=$tts_payload_size
tts_feature_manifest=$TTS_FEATURE_MANIFEST
tts_feature_manifest_sha256=$tts_feature_manifest_sha
wakeword_payload=$WAKE_PAYLOAD
wakeword_payload_sha256=$wake_payload_sha
wakeword_payload_size=$wake_payload_size
wakeword_feature_manifest=$WAKE_FEATURE_MANIFEST
wakeword_feature_manifest_sha256=$wake_feature_manifest_sha
stt_payload=$STT_PAYLOAD
stt_payload_sha256=$stt_payload_sha
stt_payload_size=$stt_payload_size
stt_feature_manifest=$STT_FEATURE_MANIFEST
stt_feature_manifest_sha256=$stt_feature_manifest_sha
assistant_payload=$ASSISTANT_PAYLOAD
assistant_payload_sha256=$assistant_payload_sha
assistant_payload_size=$assistant_payload_size
assistant_feature_manifest=$ASSISTANT_FEATURE_MANIFEST
assistant_feature_manifest_sha256=$assistant_feature_manifest_sha
ui_source=$UI_SOURCE
ui_commit=$ui_commit
ui_diff_sha256=$ui_diff_sha
ui_manifest=$UI_MANIFEST
ui_manifest_sha256=$ui_manifest_sha
ssh_enabled=$SSH_ENABLED
dropbear_sha256=$dropbear_sha
dropbearkey_sha256=$dropbearkey_sha
manifest=$RUN/manifest.json
userdata_tree=$USERDATA_TREE
userdata_tree_manifest_sha256=$userdata_manifest_sha
userdata_tree_bytes=$userdata_tree_bytes
source_offer_index=$source_offer_index
source_offer_manifest_sha256=$source_offer_manifest_sha256
verifier=$RUN/verify.log
marker_contract=PASS
ota_bootctl_sha256=$ota_bootctl_sha
ota_update_verifier_sha256=$ota_verifier_sha
ota_public_key_sha256=$ota_public_key_sha
ota_signing_mode=$OTA_SIGNING_MODE
ota_bundle=$ota_bundle
ota_bundle_sha256=$ota_bundle_sha
EOF
provenance_sha="$(sha256sum "$RUN/provenance.txt" | awk '{print $1}')"

tmp_current="$OUT/.CURRENT.$$"
cat > "$tmp_current" <<EOF
schema=1
status=PREPARED_NOT_FLASHED
image_profile=$IMAGE_PROFILE
service_profile=$SERVICE_PROFILE
feature_policy=$FEATURE_POLICY
update_channel=$UPDATE_CHANNEL
run_id=$run_id
public_release_mode=$PUBLIC_RELEASE_MODE
build_source=$PIPELINE
build_git_head=$build_head
build_git_state=$build_state
build_git_diff_sha256=$build_diffsha
product_source=$PRODUCT_SRC
product_git_head=$product_head
product_git_state=$product_state
product_git_diff_sha256=$product_diffsha
kernel_source=$KERNEL_SRC
tooling_source=$TOOLING_SRC
tooling_git_head=$tooling_head
tooling_git_state=$tooling_state
tooling_git_diff_sha256=$tooling_diffsha
kernel_git_diff_sha256=$kernel_diffsha
kernel_config=$RUN/kernel.config
kernel_config_sha256=$kernel_config_sha
expected_dtb_sha256=$IMAGE_DTB_SHA256
run_dir=$RUN
boot_image=$RUN/boot.img
boot_image_sha256=$bootsha
boot_envelope=$BOOT_ENVELOPE
boot_envelope_sha256=$(sha256sum "$BOOT_ENVELOPE" | awk '{print $1}')
components_manifest=$COMPONENTS_MANIFEST
components_manifest_sha256=$components_manifest_sha
component_timing=$RUN/component-timing.log
component_timing_sha256=$component_timing_sha
component_identities=$RUN/component-identities.log
component_identities_sha256=$component_identities_sha
provenance=$RUN/provenance.txt
provenance_sha256=$provenance_sha
busybox=$BUSYBOX_OUTPUT/busybox
busybox_sha256=$busybox_sha
busybox_source_metadata=$RUN/busybox-source.json
musl_loader=$MUSL_LOADER
musl_loader_sha256=$musl_loader_sha
musl_source_metadata=$RUN/musl-source.json
wpa_supplicant=$WPA_SUPPLICANT
wpa_supplicant_sha256=$wpa_supplicant_sha
wpa_supplicant_source_metadata=$RUN/wpa-supplicant-source.json
connectivity_source_metadata=$RUN/connectivity-source.json
tinyalsa_source_metadata=$RUN/tinyalsa-source.json
wireless_tools_source_metadata=$RUN/wireless-tools-source.json
wireless_regdb_source_metadata=$RUN/wireless-regdb-source.json
libsodium_source_metadata=$RUN/libsodium-source.json
audio_probe=$RUN/audio_probe
audio_probe_sha256=$audio_probe_sha
tinyplay=$RUN/tinyplay
tinyplay_sha256=$tinyplay_sha
tinycap=$RUN/tinycap
tinycap_sha256=$tinycap_sha
tinymix=$RUN/tinymix
tinymix_sha256=$tinymix_sha
iwconfig=$RUN/iwconfig
iwconfig_sha256=$iwconfig_sha
airplay_payload=$AIRPLAY_PAYLOAD
airplay_payload_sha256=$airplay_payload_sha
airplay_payload_size=$airplay_payload_size
airplay_feature_manifest=$AIRPLAY_FEATURE_MANIFEST
airplay_feature_manifest_sha256=$airplay_feature_manifest_sha
airplay_audio_contract=$RUN/airplay-audio-contract.log
airplay_audio_contract_sha256=$airplay_audio_contract_sha
tts_payload=$TTS_PAYLOAD
tts_payload_sha256=$tts_payload_sha
tts_payload_size=$tts_payload_size
tts_feature_manifest=$TTS_FEATURE_MANIFEST
tts_feature_manifest_sha256=$tts_feature_manifest_sha
wakeword_payload=$WAKE_PAYLOAD
wakeword_payload_sha256=$wake_payload_sha
wakeword_payload_size=$wake_payload_size
wakeword_feature_manifest=$WAKE_FEATURE_MANIFEST
wakeword_feature_manifest_sha256=$wake_feature_manifest_sha
stt_payload=$STT_PAYLOAD
stt_payload_sha256=$stt_payload_sha
stt_payload_size=$stt_payload_size
stt_feature_manifest=$STT_FEATURE_MANIFEST
stt_feature_manifest_sha256=$stt_feature_manifest_sha
assistant_payload=$ASSISTANT_PAYLOAD
assistant_payload_sha256=$assistant_payload_sha
assistant_payload_size=$assistant_payload_size
assistant_feature_manifest=$ASSISTANT_FEATURE_MANIFEST
assistant_feature_manifest_sha256=$assistant_feature_manifest_sha
ui_source=$UI_SOURCE
ui_commit=$ui_commit
ui_diff_sha256=$ui_diff_sha
ui_manifest=$UI_MANIFEST
ui_manifest_sha256=$ui_manifest_sha
ssh_enabled=$SSH_ENABLED
dropbear_sha256=$dropbear_sha
dropbearkey_sha256=$dropbearkey_sha
zimage=$RUN/zImage
zimage_sha256=$zsha
system_map=$RUN/System.map
system_map_sha256=$mapsha
manifest=$RUN/manifest.json
userdata_tree=$USERDATA_TREE
userdata_tree_manifest_sha256=$userdata_manifest_sha
userdata_tree_bytes=$userdata_tree_bytes
source_offer_index=$source_offer_index
source_offer_manifest_sha256=$source_offer_manifest_sha256
ramdisk=$RUN/boot.ramdisk.cpio.gz
ota_bootctl_sha256=$ota_bootctl_sha
ota_update_verifier_sha256=$ota_verifier_sha
ota_public_key_sha256=$ota_public_key_sha
ota_signing_mode=$OTA_SIGNING_MODE
ota_bundle=$ota_bundle
ota_bundle_sha256=$ota_bundle_sha
EOF
cp -- "$tmp_current" "$RUN/CURRENT.candidate"
if ((publish_current)); then
  mv -f -- "$tmp_current" "$OUT/CURRENT"
  ln -sfn -- "$RUN" "$OUT/current"
else
  rm -f -- "$tmp_current"
fi

echo
echo "BUILD COMPLETE"
echo "run=$RUN"
echo "boot=$RUN/boot.img"
echo "boot_sha256=$bootsha"
echo "candidate_record=$RUN/CURRENT.candidate"
echo "published_current=$publish_current"
echo "status=PREPARED_NOT_FLASHED"
