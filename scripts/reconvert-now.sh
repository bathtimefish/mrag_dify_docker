#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Re-converts every source through Docling, then synchronizes mrag as usual.
# Use after changing a Docling setting that the conversion fingerprint does not
# cover. Run ./scripts/sync-dry-run.sh first to review the plan.
exec "$root_dir/scripts/sync-now.sh" --force-reconvert "$@"
