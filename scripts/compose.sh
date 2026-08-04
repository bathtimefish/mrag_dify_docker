#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dify_docker_dir="$root_dir/vendor/dify/docker"

if [[ ! -f "$root_dir/.env" ]]; then
  echo "Missing .env. Run ./scripts/init-env.sh first." >&2
  exit 1
fi
if [[ ! -f "$dify_docker_dir/docker-compose.yaml" ]]; then
  echo "Missing official sources. Run ./scripts/bootstrap-vendors.sh first." >&2
  exit 1
fi

# The official Compose explicitly loads docker/.env into its services. Preserve
# every official default, then overlay only this project's selected settings.
python3 "$root_dir/scripts/prepare-dify-env.py" \
  "$dify_docker_dir/.env.example" \
  "$root_dir/.env" \
  "$dify_docker_dir/.env"

configured_accelerator="$(
  sed -n 's/^OLLAMA_ACCELERATOR=//p' "$root_dir/.env" |
    tail -n 1 |
    tr -d '\r'
)"
ollama_accelerator="${OLLAMA_ACCELERATOR:-${configured_accelerator:-cpu}}"
configured_docling_accelerator="$(
  sed -n 's/^DOCLING_ACCELERATOR=//p' "$root_dir/.env" |
    tail -n 1 |
    tr -d '\r'
)"
docling_accelerator="${DOCLING_ACCELERATOR:-${configured_docling_accelerator:-cpu}}"

compose_files=(
  -f docker-compose.yaml
  -f "$root_dir/compose.images.lock.yaml"
  -f "$root_dir/compose.mrag.yaml"
)
case "$ollama_accelerator" in
  cpu)
    ;;
  nvidia)
    compose_files+=(-f "$root_dir/compose.ollama.nvidia.yaml")
    ;;
  *)
    echo "Unsupported OLLAMA_ACCELERATOR: $ollama_accelerator (expected cpu or nvidia)" >&2
    exit 1
    ;;
esac
case "$docling_accelerator" in
  cpu)
    ;;
  nvidia)
    compose_files+=(-f "$root_dir/compose.docling.nvidia.yaml")
    ;;
  *)
    echo "Unsupported DOCLING_ACCELERATOR: $docling_accelerator (expected cpu or nvidia)" >&2
    exit 1
    ;;
esac

cd "$dify_docker_dir"
exec docker compose \
  --env-file "$root_dir/.env" \
  "${compose_files[@]}" \
  "$@"
