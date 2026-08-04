#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec "$root_dir/scripts/compose.sh" exec -T --user 10001:10001 \
  mrag-batch sh -c 'cd /data/mrag && exec mrag index'
