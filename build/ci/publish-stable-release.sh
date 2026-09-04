#!/usr/bin/env bash
# Publish one fully prepared stable Product release.
set -euo pipefail

: "${GH_TOKEN:?set GH_TOKEN}"
: "${RELEASE_TAG:?set RELEASE_TAG}"
: "${RELEASE_DIR:?set RELEASE_DIR}"
: "${RELEASE_NOTES:?set RELEASE_NOTES}"
: "${HEAD_SHA:?set HEAD_SHA}"

[[ "$RELEASE_TAG" =~ ^radar-puffin-v[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "ERROR: invalid stable release tag" >&2
  exit 1
}
[[ -d "$RELEASE_DIR" && ! -L "$RELEASE_DIR" ]] || {
  echo "ERROR: invalid stable release directory" >&2
  exit 1
}
[[ "$HEAD_SHA" =~ ^[0-9a-f]{40}$ ]] || {
  echo "ERROR: invalid Product commit" >&2
  exit 1
}

sums="$RELEASE_DIR/libreecho-${RELEASE_TAG}-SHA256SUMS"
[[ -f "$sums" ]] || { echo "ERROR: stable release is missing SHA256SUMS" >&2; exit 1; }
(cd "$RELEASE_DIR" && sha256sum -c "$(basename "$sums")")

mapfile -t assets < <(find "$RELEASE_DIR" -maxdepth 1 -type f -printf '%f\n' | sort)
test "${#assets[@]}" -ge 5
printf '%s\n' "${assets[@]}" | grep -qx "libreecho-${RELEASE_TAG}.ota.tar"
printf '%s\n' "${assets[@]}" | grep -qx "libreecho-${RELEASE_TAG}-initial-install.tar"
printf '%s\n' "${assets[@]}" | grep -qx "libreecho-${RELEASE_TAG}-installer.py"
printf '%s\n' "${assets[@]}" | grep -qx "libreecho-${RELEASE_TAG}-boot.img"
printf '%s\n' "${assets[@]}" | grep -qx "libreecho-${RELEASE_TAG}-SHA256SUMS"
if printf '%s\n' "${assets[@]}" | grep -Eiq 'source-offer|provenance|\.spdx\.json|source-closure'; then
  echo "ERROR: stable release contains compliance material" >&2
  exit 1
fi

alias="$RELEASE_DIR/libreecho-radar-puffin-stable.ota.tar"
[[ -f "$alias" && ! -L "$alias" ]] &&
  cmp -s "$alias" "$RELEASE_DIR/libreecho-${RELEASE_TAG}.ota.tar" || {
    echo "ERROR: stable OTA alias missing or differs from versioned bundle" >&2
    exit 1
  }
grep -Eq '^[0-9a-f]{64}  libreecho-radar-puffin-stable\.ota\.tar$' "$sums" || {
  echo "ERROR: stable OTA alias is missing from SHA256SUMS" >&2
  exit 1
}

api="repos/${GITHUB_REPOSITORY}"
verify_tag_ref() {
  gh api "$api/git/ref/tags/$RELEASE_TAG" |
    jq -e --arg sha "$HEAD_SHA" '.object.type == "commit" and .object.sha == $sha' >/dev/null
}
if ! gh api "$api/git/ref/tags/$RELEASE_TAG" >/dev/null 2>&1; then
  gh api --method POST "$api/git/refs" \
    -f ref="refs/tags/$RELEASE_TAG" -f sha="$HEAD_SHA" >/dev/null
fi
verify_tag_ref

mapfile -t release_ids < <(gh api "$api/releases?per_page=100" --paginate \
  --jq ".[] | select(.tag_name == \"$RELEASE_TAG\") | .id")
test "${#release_ids[@]}" -le 1
release_id="${release_ids[0]:-}"
if [[ -z "$release_id" ]]; then
  gh release create "$RELEASE_TAG" \
    --target "$HEAD_SHA" \
    --title "LibreEcho $RELEASE_TAG" \
    --notes-file "$RELEASE_NOTES" \
    --draft
  release_id="$(gh release view "$RELEASE_TAG" --json databaseId --jq .databaseId)"
else
  release_json="$(gh api "$api/releases/$release_id")"
  jq -e '.draft == true and .prerelease == false' <<<"$release_json" >/dev/null || {
    echo "ERROR: stable release already exists and is not a draft" >&2
    exit 1
  }
fi

gh api "$api/releases/$release_id/assets" --paginate --jq '.[].id' |
  while read -r asset_id; do
    gh api --method DELETE "$api/releases/assets/$asset_id" >/dev/null
done
for asset in "${assets[@]}"; do
  gh release upload "$RELEASE_TAG" "$RELEASE_DIR/$asset"
done

actual="$(gh release view "$RELEASE_TAG" --json assets --jq '.assets | map(.name) | sort | join("\n")')"
expected="$(printf '%s\n' "${assets[@]}" | sort)"
test "$actual" = "$expected"
gh api --method PATCH "$api/releases/$release_id" \
  -F draft=false -F prerelease=false -F make_latest=true >/dev/null
gh api "$api/releases/$release_id" |
  jq -e '.draft == false and .prerelease == false' >/dev/null
echo "stable_release_publish=PASS tag=$RELEASE_TAG assets=${#assets[@]}"
