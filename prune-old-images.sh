#!/usr/bin/env bash
# Remove old ToxTempAssistant images after a successful deploy, keeping rollbacks.
#
#   prune-old-images.sh [--dry-run] <deployed tag> [older versions to keep, default 2]
#
# For each of our images, keeps the deployed tag plus the newest older versions.
# Age is never a reason to delete: at least one older version always stays,
# however old, so a bad release can be rolled back at once, without pulling or
# building:
#
#   git checkout vX.Y.Z && GIT_TAG=vX.Y.Z docker compose --profile prod up -d
#
# Images still used by a container, running or stopped, are never removed.
# Dangling images and the build cache (nothing is built on the server) go too.
# Run by .github/workflows/deploy.yml once the new release is healthy.
set -euo pipefail

dry_run=0
if [ "${1:-}" = "--dry-run" ]; then
  dry_run=1
  shift
fi
current_tag="${1:?usage: prune-old-images.sh [--dry-run] <deployed tag> [keep]}"
keep_previous="${2:-2}"
if ! [ "$keep_previous" -ge 1 ] 2>/dev/null; then
  keep_previous=1
fi

repos=(
  ghcr.io/johannehouweling/toxtempassistant
  ghcr.io/johannehouweling/toxtempassistant-backup
  ghcr.io/johannehouweling/toxtempassistant-minio-init
)

remove() {
  if [ "$dry_run" -eq 1 ]; then
    echo "Would remove $1"
  else
    docker image rm "$1" || echo "Could not remove $1 (still in use?)"
  fi
}

for repo in "${repos[@]}"; do
  current_id=$(docker image inspect --format '{{.Id}}' "$repo:$current_tag" 2>/dev/null || true)
  if [ -z "$current_id" ]; then
    echo "$repo:$current_tag is not on this server; leaving $repo images alone."
    continue
  fi
  kept=0
  # Image ids, newest first; one image can carry several tags.
  for id in $(docker image ls --no-trunc --format '{{.CreatedAt}}|{{.ID}}' "$repo" \
    | sort -r | cut -d'|' -f2 | awk '!seen[$0]++'); do
    if [ "$id" = "$current_id" ]; then
      continue
    fi
    tags=$(docker image inspect --format '{{range .RepoTags}}{{.}} {{end}}' "$id")
    if [ "$kept" -lt "$keep_previous" ]; then
      kept=$((kept + 1))
      echo "Keeping for rollback: ${tags:-$id}"
      continue
    fi
    for tag in $tags; do
      case "$tag" in
        "$repo":*) remove "$tag" ;;
      esac
    done
  done
done

if [ "$dry_run" -eq 1 ]; then
  echo "Would remove dangling images and the build cache."
else
  docker image prune -f
  docker builder prune -af
fi
