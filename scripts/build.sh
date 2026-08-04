#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
versions_file="$root_dir/VERSIONS"

if [[ ! -f "$versions_file" ]]; then
  echo "Missing $versions_file" >&2
  exit 1
fi

# VERSIONS is a repository-controlled KEY=VALUE file.
# shellcheck disable=SC1090
source "$versions_file"

: "${MRAG_VERSION:?MRAG_VERSION is missing from VERSIONS}"
: "${PROJECT_VERSION:?PROJECT_VERSION is missing from VERSIONS}"
: "${MRAG_COMMIT:?MRAG_COMMIT is missing from VERSIONS}"
: "${SQLITE_VAPORETTO_VERSION:?SQLITE_VAPORETTO_VERSION is missing from VERSIONS}"
: "${SQLITE_VAPORETTO_AMD64_SHA256:?SQLITE_VAPORETTO_AMD64_SHA256 is missing from VERSIONS}"
: "${SQLITE_VAPORETTO_ARM64_SHA256:?SQLITE_VAPORETTO_ARM64_SHA256 is missing from VERSIONS}"
: "${PYTORCH_VERSION:?PYTORCH_VERSION is missing from VERSIONS}"

mrag_source="$root_dir/vendor/mrag"
# vendor/mrag is a submodule, so its .git is a gitlink *file*, not a directory.
# An uninitialized submodule leaves the directory present but empty.
if [[ ! -e "$mrag_source/.git" ]]; then
  echo "Missing vendor/mrag. Run ./scripts/bootstrap-vendors.sh first." >&2
  exit 1
fi

# The submodule gitlink is the authoritative pin; VERSIONS restates it so that a
# gitlink bumped without updating the image tags fails here instead of shipping.
recorded_commit="$(git -C "$root_dir" rev-parse ":vendor/mrag" 2>/dev/null || true)"
if [[ -n "$recorded_commit" && "$recorded_commit" != "$MRAG_COMMIT" ]]; then
  echo "vendor/mrag gitlink is $recorded_commit but VERSIONS says $MRAG_COMMIT" >&2
  exit 1
fi

actual_commit="$(git -C "$mrag_source" rev-parse HEAD)"
if [[ "$actual_commit" != "$MRAG_COMMIT" ]]; then
  echo "Unexpected mrag commit: $actual_commit (expected $MRAG_COMMIT)" >&2
  exit 1
fi

actual_version="$(
  python3 -c 'import pathlib,tomllib,sys; print(tomllib.loads(pathlib.Path(sys.argv[1]).read_text())["project"]["version"])' \
    "$mrag_source/pyproject.toml"
)"
if [[ "$actual_version" != "$MRAG_VERSION" ]]; then
  echo "Unexpected mrag version: $actual_version (expected $MRAG_VERSION)" >&2
  exit 1
fi

runtime_image="local/mrag-runtime:$MRAG_VERSION"
batch_image="local/mrag-batch:$MRAG_VERSION"

echo "Building $runtime_image from mrag $MRAG_VERSION ($MRAG_COMMIT)"
docker build \
  --file "$root_dir/mrag/Dockerfile" \
  --build-arg "MRAG_SOURCE_COMMIT=$MRAG_COMMIT" \
  --build-arg "PROJECT_VERSION=$PROJECT_VERSION" \
  --build-arg "PYTORCH_VERSION=$PYTORCH_VERSION" \
  --build-arg "SQLITE_VAPORETTO_VERSION=$SQLITE_VAPORETTO_VERSION" \
  --build-arg "SQLITE_VAPORETTO_AMD64_SHA256=$SQLITE_VAPORETTO_AMD64_SHA256" \
  --build-arg "SQLITE_VAPORETTO_ARM64_SHA256=$SQLITE_VAPORETTO_ARM64_SHA256" \
  --tag "$runtime_image" \
  "$root_dir"

echo "Building $batch_image from $runtime_image"
docker build \
  --file "$root_dir/batch/Dockerfile" \
  --build-arg "MRAG_RUNTIME_IMAGE=$runtime_image" \
  --build-arg "MRAG_SOURCE_COMMIT=$MRAG_COMMIT" \
  --build-arg "PROJECT_VERSION=$PROJECT_VERSION" \
  --tag "$batch_image" \
  "$root_dir"

docker image inspect "$runtime_image" "$batch_image" >/dev/null
echo "Custom images are ready:"
for image in "$runtime_image" "$batch_image"; do
  docker image ls --format '  {{.Repository}}:{{.Tag}}\t{{.Size}}' "$image"
done
