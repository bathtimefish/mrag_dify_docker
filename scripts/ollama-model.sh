#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose="$root_dir/scripts/compose.sh"
profiles_dir="$root_dir/mrag_profiles"
env_file="$root_dir/.env"

usage() {
  cat >&2 <<'EOF'
Usage: ollama-model.sh <command> [args]

Manage LLM models inside the Ollama container.

Commands:
  list                     List models present in the Ollama container.
  pull <model>             Pull <model> only if it is not already present.
  remove <model> [--force] Remove <model>. Refuses to remove a model referenced
                           by an mrag profile (the embedding model, or the
                           augmentation model when augmentation is enabled)
                           unless --force is given.
EOF
}

run_ollama() {
  "$compose" exec -T ollama ollama "$@"
}

normalize() {
  case "$1" in
    *:*) printf '%s\n' "$1" ;;
    *)   printf '%s:latest\n' "$1" ;;
  esac
}

list_present_normalized() {
  # `ollama list` prints a header row followed by "NAME  ID  SIZE  MODIFIED".
  run_ollama list | awk 'NR > 1 && $1 != "" { print $1 }' | while IFS= read -r name; do
    normalize "$name"
  done
}

model_present() {
  local target
  target="$(normalize "$1")"
  list_present_normalized | grep -Fxq -- "$target"
}

protected_models() {
  python3 "$root_dir/scripts/ollama_model_guard.py" "$profiles_dir" "$env_file"
}

cmd_pull() {
  local model="${1:-}"
  if [[ -z "$model" ]]; then
    echo "pull requires a model name." >&2
    usage
    exit 2
  fi
  if model_present "$model"; then
    echo "Model '$model' is already present; skipping pull."
    return 0
  fi
  echo "Pulling '$model' ..."
  run_ollama pull "$model"
}

cmd_remove() {
  local model="" force="false" arg
  for arg in "$@"; do
    case "$arg" in
      --force) force="true" ;;
      -*) echo "Unknown option: $arg" >&2; usage; exit 2 ;;
      *)
        if [[ -z "$model" ]]; then
          model="$arg"
        else
          echo "Unexpected argument: $arg" >&2
          exit 2
        fi
        ;;
    esac
  done
  if [[ -z "$model" ]]; then
    echo "remove requires a model name." >&2
    usage
    exit 2
  fi

  local target
  target="$(normalize "$model")"
  if protected_models | grep -Fxq -- "$target"; then
    if [[ "$force" != "true" ]]; then
      echo "Refusing to remove '$model': it is referenced by an mrag profile" >&2
      echo "(embedding or augmentation model). Removing it will break indexing" >&2
      echo "and retrieval. Re-run with --force to override." >&2
      exit 1
    fi
    echo "WARNING: '$model' is referenced by an mrag profile; removing anyway (--force)."
  fi
  echo "Removing '$model' ..."
  run_ollama rm "$model"
}

main() {
  local command="${1:-}"
  shift || true
  case "$command" in
    list) run_ollama list ;;
    pull) cmd_pull "$@" ;;
    remove) cmd_remove "$@" ;;
    -h | --help | help) usage ;;
    "") usage; exit 2 ;;
    *) echo "Unknown command: $command" >&2; usage; exit 2 ;;
  esac
}

main "$@"
