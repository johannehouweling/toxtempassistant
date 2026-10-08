#!/usr/bin/env bash
# Compare the API contract with the base branch.
#
# Frozen versions (openapi/v<N>.yaml) may only grow: oasdiff fails on anything that
# can break a client, and a frozen file may not be deleted. The preview contract
# (openapi/preview.yaml) is allowed to change; its changes are only listed.
#
# Usage: check-api-contract.sh <base-ref> <spec-dir> [oasdiff-binary]
set -euo pipefail

base_ref="$1"
spec_dir="${2%/}"
oasdiff="${3:-oasdiff}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
failed=0

# --fail-on WARN: oasdiff reports a removed response field as a warning, which would
# pass at ERR. --flatten-allof compares what schemas combine to, not branch by branch.
frozen=$(git ls-tree --name-only "$base_ref" "$spec_dir/" | grep -E '/v[0-9]+\.yaml$' || true)
for path in $frozen; do
  if [ ! -f "$path" ]; then
    echo "::error file=$path::Frozen API contract $path was deleted"
    failed=1
    continue
  fi
  git show "$base_ref:$path" > "$tmp/base.yaml"
  echo "Checking frozen contract $path against $base_ref"
  if ! "$oasdiff" breaking "$tmp/base.yaml" "$path" \
      --fail-on WARN --flatten-allof --format githubactions; then
    failed=1
  fi
done

preview="$spec_dir/preview.yaml"
if [ -f "$preview" ] && git cat-file -e "$base_ref:$preview" 2>/dev/null; then
  git show "$base_ref:$preview" > "$tmp/preview-base.yaml"
  {
    echo "### Preview API contract changes (informational)"
    "$oasdiff" changelog "$tmp/preview-base.yaml" "$preview" --format markdown \
      || true
  } >> "${GITHUB_STEP_SUMMARY:-/dev/stdout}"
fi

exit "$failed"
