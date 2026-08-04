#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
vendor_dir="$root_dir/vendor"
dify_commit="5c6372d2f76d240265b92fd27c16bc772ffcb107"

mkdir -p "$vendor_dir"

# Dify is a plain pinned checkout: it is upstream code this project never edits,
# and a shallow single-branch clone keeps the working tree small.
if [[ ! -d "$vendor_dir/dify/.git" ]]; then
  git clone --depth 1 --branch 1.16.0 \
    https://github.com/langgenius/dify.git "$vendor_dir/dify"
fi
if [[ "$(git -C "$vendor_dir/dify" rev-parse HEAD)" != "$dify_commit" ]]; then
  echo "Unexpected Dify commit. Expected $dify_commit" >&2
  exit 1
fi

# mrag is a git submodule. Its commit is recorded in the gitlink rather than
# here, so this only materializes the checkout the repository already pins.
git -C "$root_dir" submodule update --init -- vendor/mrag

echo "Official source checkouts are ready."

