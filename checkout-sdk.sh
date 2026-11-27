#!/bin/bash
set -euo pipefail
repo=$1; ref=$2; dest=$3
if [ ! -d "$dest/.git" ]; then
  git init -q "$dest"
  git -C "$dest" remote add origin "https://github.com/absmartly/$repo.git"
fi
git -C "$dest" fetch --depth 1 origin "$ref"
resolved=$(git -C "$dest" rev-parse FETCH_HEAD)
if [[ "$ref" =~ ^[0-9a-f]{40}$ ]] && [ "$resolved" != "$ref" ]; then
  echo "SHA mismatch: $resolved != $ref" >&2; exit 1
fi
git -C "$dest" checkout --detach -f "$resolved"
test "$(git -C "$dest" rev-parse HEAD)" = "$resolved"
