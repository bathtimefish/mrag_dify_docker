#!/usr/bin/env bash
set -euo pipefail

cd "${MRAG_PROJECT_DIR:-/data/mrag}"
exec mrag serve --host 0.0.0.0 --port 8000

