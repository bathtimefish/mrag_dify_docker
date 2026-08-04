#!/bin/sh
set -eu

model="${OLLAMA_MODEL:-bge-m3:latest}"
expected="${OLLAMA_MODEL_MANIFEST_SHA256:?OLLAMA_MODEL_MANIFEST_SHA256 is required}"
storage_root="${OLLAMA_STORAGE_ROOT:-/models}"
offline="${MODEL_DOWNLOAD_OFFLINE:-false}"

case "$model" in
  *..*|/*|*:*:*)
    echo "Unsupported Ollama model reference: $model" >&2
    exit 2
    ;;
esac

repository="${model%:*}"
tag="${model##*:}"
if [ "$repository" = "$model" ]; then
  tag="latest"
fi
case "$repository" in
  */*) manifest_repository="$repository" ;;
  *) manifest_repository="library/$repository" ;;
esac
manifest="$storage_root/models/manifests/registry.ollama.ai/$manifest_repository/$tag"

manifest_matches() {
  [ -f "$manifest" ] && [ "$(sha256sum "$manifest" | cut -d ' ' -f 1)" = "$expected" ]
}

if manifest_matches; then
  echo "Pinned Ollama model is already available: $model ($expected)"
  exit 0
fi

case "$(printf '%s' "$offline" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    echo "Pinned Ollama model is unavailable and offline mode is enabled: $model" >&2
    exit 1
    ;;
  0|false|no|off) ;;
  *) echo "MODEL_DOWNLOAD_OFFLINE must be true or false" >&2; exit 2 ;;
esac

attempt=1
while [ "$attempt" -le 5 ]; do
  if ollama pull "$model" && manifest_matches; then
    echo "Pinned Ollama model is ready: $model ($expected)"
    exit 0
  fi
  if [ -f "$manifest" ]; then
    actual="$(sha256sum "$manifest" | cut -d ' ' -f 1)"
    echo "Ollama manifest mismatch: expected $expected, got $actual" >&2
  else
    echo "Ollama model pull did not create the expected manifest" >&2
  fi
  if [ "$attempt" -lt 5 ]; then
    delay=$((attempt * 5))
    echo "Retrying model pull in ${delay}s (attempt $attempt/5)" >&2
    sleep "$delay"
  fi
  attempt=$((attempt + 1))
done

echo "Unable to obtain the pinned Ollama model: $model" >&2
exit 1
