#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp)"
trap 'rm -f "$temporary"' EXIT

"$root_dir/scripts/compose.sh" config --format json >"$temporary"
python3 "$root_dir/scripts/verify_release.py" \
  --root "$root_dir" \
  --compose-json "$temporary"
