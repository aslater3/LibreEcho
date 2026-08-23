#!/usr/bin/env bash
# Print the checkout-path-independent identity of AirPlay sysroot inputs that
# are actually consumed by the AirPlay and assistant-curl builders.
set -euo pipefail

REPO="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
CACHE_TOOL="$REPO/component-cache.py"
sysroot="${1:?usage: airplay-sysroot-key.sh <airplay-sysroot>}"

[[ -f "$CACHE_TOOL" && ! -L "$CACHE_TOOL" ]] || {
  echo "ERROR: component cache tool is missing: $CACHE_TOOL" >&2
  exit 1
}
[[ -d "$sysroot" && ! -L "$sysroot" ]] || {
  echo "ERROR: AirPlay sysroot is missing or unsafe: $sysroot" >&2
  exit 1
}

key_args=(
  --tree "airplay-sysroot-include=$sysroot/usr/include"
  --tree "airplay-sysroot-lib=$sysroot/usr/lib"
  --file "airplay-avahi-daemon=$sysroot/usr/sbin/avahi-daemon"
  --file "airplay-dbus-daemon=$sysroot/usr/bin/dbus-daemon"
  --file "airplay-avahi-config=$sysroot/etc/avahi/avahi-daemon.conf"
  --tree "airplay-dbus-config=$sysroot/usr/share/dbus-1"
)

share_pkgconfig="$sysroot/usr/share/pkgconfig"
if [[ -d "$share_pkgconfig" && ! -L "$share_pkgconfig" ]]; then
  key_args+=(
    --value "airplay-share-pkgconfig=present"
    --tree "airplay-share-pkgconfig=$share_pkgconfig"
  )
elif [[ -e "$share_pkgconfig" || -L "$share_pkgconfig" ]]; then
  echo "ERROR: AirPlay shared pkg-config input is unsafe: $share_pkgconfig" >&2
  exit 1
else
  key_args+=(--value "airplay-share-pkgconfig=absent")
fi

copyright_index=0
while IFS= read -r -d '' copyright; do
  copyright_package="$(basename -- "$(dirname -- "$copyright")")"
  key_args+=(
    --value "airplay-copyright-package-$copyright_index=$copyright_package"
    --file "airplay-copyright-$copyright_index=$copyright"
  )
  copyright_index=$((copyright_index + 1))
done < <(
  find "$sysroot/usr/share/doc" -mindepth 2 -maxdepth 2 \
    -type f -name copyright -print0 | LC_ALL=C sort -z
)
((copyright_index > 0)) || {
  echo "ERROR: AirPlay sysroot contains no dependency copyright records" >&2
  exit 1
}

exec python3 -B "$CACHE_TOOL" key --component airplay-sysroot "${key_args[@]}"
