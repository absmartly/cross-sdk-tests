#!/bin/bash
# Callers own repository initialization, remote configuration and working-tree policy.
fetch_and_checkout_revision() {
  local repo="$1" remote="$2" revision="$3"
  git -C "$repo" fetch -q --depth 1 "$remote" "$revision" &&
    git -C "$repo" checkout -q FETCH_HEAD
}
