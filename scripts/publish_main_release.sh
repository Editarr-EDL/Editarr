#!/usr/bin/env bash
# Publish the verified macOS build with a write-scoped GitHub token.
set -euo pipefail

version=${1:?provide an Editarr version}
revision=${2:?provide the generated release commit}
distribution=${3:?provide the downloaded distribution directory}
notes=${4:?provide the generated release notes}
repo=${GITHUB_REPOSITORY:?provide the GitHub repository}
tag="v$version"
base="Editarr-$tag-macOS"
assets=("$base-arm64.dmg" "$base-Notices.tar.gz" "$base-Third-Party-Source.tar.gz" SHA256SUMS.txt)

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$revision" =~ ^[0-9a-f]{40}$ ]]
[[ -s "$notes" ]]
jq -e --arg version "$version" --arg revision "$revision" '
  .version == $version and .source_revision == $revision and .target == "macos-arm64"
  and .failures == [] and .checks.scratch_cleanup.removed == true
' "$distribution/installed-verification.json" >/dev/null
(
  cd "$distribution/assets"
  shasum -a 256 "${assets[@]:0:3}" | diff - SHA256SUMS.txt
)

expected=$(for asset in "${assets[@]}"; do
  file="$distribution/assets/$asset"
  size=$(wc -c < "$file")
  digest=$(shasum -a 256 "$file" | cut -d ' ' -f 1)
  jq -n --arg name "$asset" --argjson size "$size" --arg digest "sha256:$digest" \
    '{name: $name, size: $size, digest: $digest, state: "uploaded"}'
done | jq -s 'sort_by(.name)')

refs=$(git ls-remote origin "refs/tags/$tag" "refs/tags/$tag^{}")
remote_commit=$(awk '
  /\^\{\}$/ { peeled=$1; next }
  { object=$1 }
  END { print peeled ? peeled : object }
' <<< "$refs")
if [[ -z "$remote_commit" ]]; then
  git push origin "$revision:refs/tags/$tag"
elif [[ "$remote_commit" != "$revision" ]]; then
  echo "$tag already points to $remote_commit, expected $revision" >&2
  exit 1
fi

if release=$(gh release view "$tag" --repo "$repo" --json assets,isDraft,url); then
  if [[ $(jq -r '.isDraft' <<< "$release") == false ]]; then
    jq -e --argjson expected "$expected" '
      ([.assets[].name] | sort) == [$expected[].name]
      and all(.assets[]; .state == "uploaded" and (.digest | startswith("sha256:")))
    ' <<< "$release" >/dev/null
    jq -r '.url' <<< "$release"
    exit 0
  fi
else
  gh release create "$tag" --repo "$repo" --verify-tag --draft \
    --title "Editarr $tag" --notes-file "$notes"
fi

files=()
for asset in "${assets[@]}"; do files+=("$distribution/assets/$asset"); done
gh release upload "$tag" --repo "$repo" --clobber "${files[@]}"
release=$(gh release view "$tag" --repo "$repo" --json assets,isDraft)
jq -e --argjson expected "$expected" '
  .isDraft and ([.assets[] | {name, size, digest, state}] | sort_by(.name)) == $expected
' <<< "$release" >/dev/null

# A retry of an older commit must not take over the Latest download.
published=$(gh api --paginate "repos/$repo/releases" \
  --jq '.[] | select(.draft == false and .prerelease == false) | .tag_name')
newest=$(printf '%s\n%s\n' "$tag" "$published" | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)
latest=false
if [[ "$newest" == "$tag" ]]; then latest=true; fi
gh release edit "$tag" --repo "$repo" --verify-tag --draft=false --latest="$latest"
