#!/usr/bin/env bash
set -euo pipefail

project_dir="${MRAG_PROJECT_DIR:-/data/mrag}"
profiles_dir="$project_dir/profiles"
model_cache_dir="${HF_HOME:-/home/mrag/.cache/huggingface}"
project_name="${MRAG_PROJECT_NAME:-main-kb}"
knowledge_id="${MRAG_KNOWLEDGE_ID:-main_kb}"
profile_existed=false
if [[ -f "$profiles_dir/default.yaml" ]]; then
  profile_existed=true
fi

mkdir -p "$project_dir"
mkdir -p "$model_cache_dir"
if [[ ! -f "$project_dir/mrag.yaml" ]]; then
  # Initialize outside the bind mount, then copy project state and only missing
  # default files. This never writes to an existing host-owned profile file.
  seed_dir="$(mktemp -d)"
  trap 'rm -rf "$seed_dir"' EXIT
  mrag init "$seed_dir" \
    --name "$project_name" \
    --kb-id "$knowledge_id" \
    --non-interactive
  mkdir -p "$profiles_dir"
  for profile_file in default.yaml context_prompt.txt; do
    if [[ ! -f "$profiles_dir/$profile_file" ]]; then
      cp "$seed_dir/profiles/$profile_file" "$profiles_dir/$profile_file"
    fi
  done
  rm -rf "$seed_dir/profiles"
  cp -a "$seed_dir/." "$project_dir/"
  rm -rf "$seed_dir"
  trap - EXIT
elif [[ ! -f "$profiles_dir/default.yaml" ||
        ! -f "$profiles_dir/context_prompt.txt" ]]; then
  # The profiles directory is a host bind mount. It can be empty even when the
  # named project volume already contains mrag.yaml (for example after upgrading
  # from a release that kept profiles inside the volume). Generate an isolated
  # project and restore only missing profile files, preserving user edits and DB.
  seed_dir="$(mktemp -d)"
  trap 'rm -rf "$seed_dir"' EXIT
  mrag init "$seed_dir" \
    --name "$project_name" \
    --kb-id "$knowledge_id" \
    --non-interactive
  mkdir -p "$profiles_dir"
  for profile_file in default.yaml context_prompt.txt; do
    if [[ ! -f "$profiles_dir/$profile_file" ]]; then
      cp "$seed_dir/profiles/$profile_file" "$profiles_dir/$profile_file"
    fi
  done
  rm -rf "$seed_dir"
  trap - EXIT
fi

case "${MRAG_RERANK_ENABLED:-true}" in
  1|true|TRUE|yes|YES|on|ON)
    pinned_reranker_path="$(python /app/mrag/pin_reranker.py)"
    export MRAG_RERANK_MODEL="$pinned_reranker_path"
    ;;
esac

MRAG_PRESERVE_EXISTING_PROFILE="$profile_existed" \
  python /app/mrag/configure_project.py
python /app/mrag/verify_runtime.py
# The profiles bind mount is host-owned configuration. Do not change either its
# contents or metadata while fixing ownership of the named project volume.
find "$project_dir" \
  -path "$profiles_dir" -prune -o \
  -exec chown mrag:mrag {} +
chown -R mrag:mrag "$(dirname "$model_cache_dir")"
