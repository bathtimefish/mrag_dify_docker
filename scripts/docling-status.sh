#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec "$root_dir/scripts/compose.sh" exec -T --user mrag \
  mrag-batch python /app/batch/docling_admin.py status \
  --converted-dir /data/converted-docs "$@"
