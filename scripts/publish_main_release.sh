#!/usr/bin/env bash
# Verify the native distribution for one revision and publish it from main.
set -euo pipefail

version=${1:?provide an Editarr version}
revision=${2:?provide the generated release commit}
distribution=${3:?provide the downloaded distribution directory}
notes=${4:?provide the generated release notes}
mode=${5:-}
tag="v$version"

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && "$revision" =~ ^[0-9a-f]{40}$ ]]
[[ "$mode" == "" || "$mode" == --verify-only ]]
[[ -s "$notes" ]]

macos="$distribution/editarr-$tag-macos"
base="Editarr-$tag-macOS"
mac_assets=("$base-arm64.dmg" "$base-Notices.tar.gz" "$base-Third-Party-Source.tar.gz")
jq -e --arg version "$version" --arg revision "$revision" '
  .version == $version and .source_revision == $revision and .target == "macos-arm64"
  and .failures == [] and .checks.scratch_cleanup.removed == true
' "$macos/installed-verification.json" >/dev/null
(
  cd "$macos/assets"
  shasum -a 256 "${mac_assets[@]}" | diff - SHA256SUMS.txt
)

files=()
for name in "${mac_assets[@]}"; do files+=("$macos/assets/$name"); done

manifest="$distribution/SHA256SUMS.txt"
cp "$macos/assets/SHA256SUMS.txt" "$manifest"

add_alpha_assets() {
  local platform=$1 label=$2 target=$3 installer=$4 name file digest
  local artifacts="$distribution/editarr-$tag-$platform"
  local evidence="$artifacts/artifact-verification.json"
  jq -e --arg version "$version" --arg revision "$revision" --arg target "$target" '
    .version == $version and .source_revision == $revision and .target == $target
  ' "$evidence" >/dev/null
  for name in "$installer" "Editarr-$tag-$label-Notices.tar.gz" \
    "Editarr-$tag-$label-Third-Party-Source.tar.gz"; do
    file="$artifacts/$name"
    [[ -s "$file" ]]
    digest=$(shasum -a 256 "$file" | cut -d ' ' -f 1)
    jq -e --arg name "$name" --arg digest "$digest" '.sha256[$name] == $digest' \
      "$evidence" >/dev/null
    printf '%s  %s\n' "$digest" "$name" >> "$manifest"
    files+=("$file")
  done
}

add_alpha_assets windows Windows windows-x64 "Editarr-$tag-Windows-x64-Setup.exe"
add_alpha_assets linux Linux linux-x86_64 "Editarr-$tag-Linux-x86_64.AppImage"
files+=("$manifest")
cat "$manifest"
if [[ "$mode" == --verify-only ]]; then exit 0; fi

repo=${GITHUB_REPOSITORY:?provide the GitHub repository}
expected=$(
  manifest_digest=$(shasum -a 256 "$manifest" | cut -d ' ' -f 1)
  for file in "${files[@]}"; do
    name=${file##*/}
    size=$(wc -c < "$file")
    if [[ "$file" == "$manifest" ]]; then
      digest=$manifest_digest
    else
      digest=$(awk -v name="$name" '$2 == name { print $1 }' "$manifest")
    fi
    jq -n --arg name "$name" --argjson size "$size" --arg digest "sha256:$digest" \
      '{name: $name, size: $size, digest: $digest, state: "uploaded"}'
  done | jq -s 'sort_by(.name)'
)

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
gh release edit "$tag" --repo "$repo" --verify-tag --draft=false --latest="$latest" --notes-file "$notes"
