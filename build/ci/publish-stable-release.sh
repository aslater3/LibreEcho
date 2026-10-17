#!/usr/bin/env bash
# Publish one fully prepared stable Product release.
set -euo pipefail

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
# The pre-mutation gate validates libreecho-${RELEASE_TAG}.ota.tar and the full
# immutable asset set before this script can call gh.
python3 "$(dirname "$0")/validate-stable-publisher.py" \
  --release-dir "$RELEASE_DIR" --release-tag "$RELEASE_TAG"

mapfile -t assets < <(find "$RELEASE_DIR" -maxdepth 1 -type f -printf '%f\n' | sort)

if [[ "${PUBLISH_DRY_RUN:-0}" == 1 ]]; then
  echo "stable_release_publish=PREPARED_NOT_PUBLISHED tag=$RELEASE_TAG assets=${#assets[@]}"
  exit 0
fi
: "${GH_TOKEN:?set GH_TOKEN}"

# GitHub "Latest" is an explicit maintainer choice made at stable dispatch
# (build-release.yml input make_latest) and carried in the verified build's
# release-request.json. RELEASE_MAKE_LATEST overrides it for callers that
# pass the value directly. Nothing defaults to true: an absent or malformed
# choice fails before the first GitHub call. Dry runs never publish and
# therefore do not need the choice.
make_latest="${RELEASE_MAKE_LATEST:-}"
if [[ -z "$make_latest" ]]; then
  request="${RELEASE_REQUEST:-}"
  if [[ -z "$request" ]]; then
    mapfile -t requests < <(find "${RELEASE_ARTIFACT_ROOT:-verified-build}" \
      -name release-request.json -type f -print 2>/dev/null)
    [[ "${#requests[@]}" -eq 1 ]] || {
      echo "ERROR: expected one release-request.json carrying make_latest" >&2
      exit 1
    }
    request="${requests[0]}"
  fi
  [[ -f "$request" && ! -L "$request" ]] || {
    echo "ERROR: invalid release request path" >&2
    exit 1
  }
  make_latest="$(jq -r 'if (.make_latest | type) == "boolean" then .make_latest | tostring else "invalid" end' "$request")"
fi
[[ "$make_latest" == true || "$make_latest" == false ]] || {
  echo "ERROR: make_latest must be true or false" >&2
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
    --latest="$make_latest" \
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
  -F draft=false -F prerelease=false -f make_latest="$make_latest" >/dev/null
gh api "$api/releases/$release_id" |
  jq -e '.draft == false and .prerelease == false' >/dev/null
latest_tag="$(gh api "$api/releases/latest" --jq .tag_name 2>/dev/null)" || latest_tag=""
if [[ "$make_latest" == true ]]; then
  [[ "$latest_tag" == "$RELEASE_TAG" ]] || {
    echo "ERROR: $RELEASE_TAG was not marked Latest (Latest is $latest_tag)" >&2
    exit 1
  }
else
  [[ "$latest_tag" != "$RELEASE_TAG" ]] || {
    echo "ERROR: $RELEASE_TAG became Latest although make_latest=false" >&2
    exit 1
  }
fi
echo "stable_release_publish=PASS tag=$RELEASE_TAG assets=${#assets[@]} make_latest=$make_latest latest=$latest_tag"
