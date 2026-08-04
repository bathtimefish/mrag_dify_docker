#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Reports the effective Docling settings and probes the server. Pass --no-probe
# to report settings only, without any network access.
exec "$root_dir/scripts/compose.sh" exec -T --user mrag \
  mrag-batch python /app/batch/docling_admin.py doctor "$@"
