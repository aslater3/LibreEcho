#!/usr/bin/env bash
# Create the credential-free Wi-Fi profile used by public release images.
set -euo pipefail

OUTPUT=${1:?usage: ./prepare-public-wifi-config.sh OUTPUT}
[[ "$OUTPUT" != /*/../* && "$OUTPUT" != */../* ]] || {
  echo "ERROR: unsafe output path" >&2
  exit 1
}
[[ ! -e "$OUTPUT" ]] || {
  echo "ERROR: refusing to overwrite existing output: $OUTPUT" >&2
  exit 1
}

umask 077
cat >"$OUTPUT" <<'EOF'
# Public release profile: credentials are supplied during first-boot setup.
ctrl_interface=/tmp/wpa_ctrl
update_config=1
country=GB
EOF
chmod 0600 "$OUTPUT"

if grep -Eq '^[[:space:]]*(ssid|psk)[[:space:]]*=' "$OUTPUT"; then
  echo "ERROR: public Wi-Fi profile contains credentials" >&2
  exit 1
fi
printf 'PUBLIC_WIFI_CONFIG_READY=%s\n' "$OUTPUT"
