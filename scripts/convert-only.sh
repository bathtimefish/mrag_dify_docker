#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Runs the Docling conversion phase and stops before touching mrag. The
# converted tree and its manifest are updated; the index is left untouched.
# Inspect the outcome with ./scripts/docling-status.sh.
exec "$root_dir/scripts/sync-now.sh" --convert-only "$@"
