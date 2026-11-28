#!/bin/bash
# Callers own repository initialization, remote configuration and working-tree policy.
fetch_and_checkout_revision() {
  local repo="$1" remote="$2" revision="$3"
  git -C "$repo" fetch -q --depth 1 "$remote" "$revision" &&
    git -C "$repo" checkout -q FETCH_HEAD
}

# Check out one SDK input at its pin as <dest>: SDK_REVISIONS (an exact commit) wins over SDK_BRANCHES,
# and SDK_REPO_SOURCES maps a checkout directory to the repository it is cloned from. Requires
# sdk-branches.sh to be sourced. CI uses it for every job; set SDK_CLONE_PREFIX to add credentials.
checkout_sdk() {
  local repo="$1" dest="$2"
  local branch="${SDK_BRANCHES[$repo]:-main}" revision="${SDK_REVISIONS[$repo]:-}"
  local source_repo="${SDK_REPO_SOURCES[$repo]:-$repo}"
  local url="${SDK_CLONE_PREFIX:-https://}github.com/absmartly/$source_repo.git"
  if [ -n "$revision" ]; then
    git init -q "$dest" && fetch_and_checkout_revision "$dest" "$url" "$revision"
  else
    git clone -q --depth 1 --branch "$branch" "$url" "$dest"
  fi
}
