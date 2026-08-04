#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose="$root_dir/scripts/compose.sh"
timeout_seconds="${VERIFY_TIMEOUT_SECONDS:-240}"

expected_services=(
  agent_backend
  api
  api_websocket
  db_postgres
  docling-serve
  local_sandbox
  mrag-api
  mrag-batch
  mrag-qdrant
  nginx
  ollama
  plugin_daemon
  qdrant
  redis
  sandbox
  ssrf_proxy
  web
  worker
  worker_beat
)

deadline=$((SECONDS + timeout_seconds))
while true; do
  running="$("$compose" ps --services --status running)"
  missing=()
  for service in "${expected_services[@]}"; do
    if ! grep -Fxq "$service" <<<"$running"; then
      missing+=("$service")
    fi
  done

  pending_health="$(
    "$compose" ps --format json |
      python3 -c '
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit
try:
    records = json.loads(raw)
    if isinstance(records, dict):
        records = [records]
except json.JSONDecodeError:
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
for item in records:
    health = (item.get("Health") or "").lower()
    if health in {"starting", "unhealthy"}:
        name = item.get("Service") or item.get("Name")
        print(f"{name}: {health}")
'
  )"

  if ((${#missing[@]} == 0)) && [[ -z "$pending_health" ]]; then
    break
  fi
  if ((SECONDS >= deadline)); then
    echo "Timed out waiting for the stack." >&2
    ((${#missing[@]})) && printf 'Not running: %s\n' "${missing[*]}" >&2
    [[ -n "$pending_health" ]] && printf 'Health not ready:\n%s\n' "$pending_health" >&2
    "$compose" ps >&2
    exit 1
  fi
  sleep 5
done

"$compose" ps --all --format json ollama-model-init mrag-init |
  python3 -c '
import json, sys

expected = {"ollama-model-init", "mrag-init"}
seen = set()
raw = sys.stdin.read().strip()
try:
    records = json.loads(raw)
    if isinstance(records, dict):
        records = [records]
except json.JSONDecodeError:
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
for item in records:
    service = item.get("Service")
    seen.add(service)
    if item.get("State") != "exited" or int(item.get("ExitCode", -1)) != 0:
        raise SystemExit(f"{service} did not complete successfully")
missing = expected - seen
if missing:
    raise SystemExit(f"missing initialization services: {sorted(missing)}")
'

curl --fail --silent --show-error \
  --retry 12 --retry-delay 2 --retry-connrefused \
  "http://127.0.0.1/install" >/dev/null
curl --fail --silent --show-error \
  --retry 12 --retry-delay 2 --retry-connrefused \
  "http://127.0.0.1/console/api/setup" >/dev/null
curl --fail --silent --show-error \
  --retry 12 --retry-delay 2 --retry-connrefused \
  "http://127.0.0.1:${MRAG_EXPOSE_PORT:-8000}/openapi.json" >/dev/null

# Verify the internal-only Docling endpoint, authentication, and pinned version.
"$compose" exec -T docling-serve python -c '
import json
import os
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:5001/version",
    headers={"X-Api-Key": os.environ["DOCLING_SERVE_API_KEY"]},
)
with urllib.request.urlopen(request, timeout=10) as response:
    payload = json.load(response)
if payload.get("docling-serve") != "1.28.0":
    raise SystemExit(f"unexpected docling-serve version: {payload!r}")
if payload.get("docling") != "2.115.0":
    raise SystemExit(f"unexpected Docling version: {payload!r}")
'

"$compose" exec -T mrag-batch python -c '
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app/batch")
from docling_http_client import HttpDoclingClient
from docling_ingestion import DoclingSettings, SourceDocument

with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "docling-smoke.txt"
    path.write_text("Docling verification marker", encoding="utf-8")
    stat = path.stat()
    result = HttpDoclingClient().convert(
        SourceDocument(
            relative_path=path.name,
            absolute_path=path,
            sha256="verify-only",
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        ),
        DoclingSettings.from_environment(),
    )
if result.status.value != "success":
    raise SystemExit(f"Docling smoke conversion failed: {result.status.value}")
if "Docling verification marker" not in (result.markdown or ""):
    raise SystemExit("Docling smoke conversion returned unexpected Markdown")
'

# The default profile must keep reranking enabled. A healthy API additionally
# proves that the configured CrossEncoder model was loaded successfully,
# because mrag loads it before starting Uvicorn.
"$compose" exec -T mrag-api python -c '
from pathlib import Path
from mrag.config.profile import load_profile

project_dir = Path("/data/mrag")
profile = load_profile("default", project_dir)
if not profile.rerank.enabled:
    raise SystemExit("default profile reranking is disabled")
print(f"Reranker ready: {profile.rerank.model}")
'

# Reproduce Dify's External Knowledge registration probe. The API key stays
# inside the pipe between containers and is never printed. mrag returns 422
# because Dify's probe intentionally has no JSON body; Dify accepts this as a
# reachable, authorized endpoint.
registration_status="$(
  # The key expression expands inside the container, not in the host shell.
  # shellcheck disable=SC2016
  "$compose" exec -T mrag-api sh -c 'printf %s "$MRAG_API_KEY"' |
    "$compose" exec -T api python -c '
import sys
from core.helper import ssrf_proxy
key = sys.stdin.read()
response = ssrf_proxy.post(
    "http://mrag-api:8000/retrieval",
    headers={"Authorization": "Bearer " + key},
)
print(response.status_code)
'
)"
if [[ "$registration_status" != "422" ]]; then
  echo "Unexpected Dify-to-mrag registration status: $registration_status" >&2
  exit 1
fi

# Celery must use the same Redis credential as the other Dify components.
# A running worker container is not sufficient: Celery can remain alive while
# retrying an invalid broker URL for several hours.
"$compose" exec -T worker python -c '
import os
from urllib.parse import urlparse
import redis

broker = urlparse(os.environ["CELERY_BROKER_URL"])
if broker.password != os.environ.get("REDIS_PASSWORD"):
    raise SystemExit("CELERY_BROKER_URL password does not match REDIS_PASSWORD")
client = redis.Redis(
    host=broker.hostname,
    port=broker.port or 6379,
    password=broker.password,
    db=int(broker.path.lstrip("/") or "0"),
)
if not client.ping():
    raise SystemExit("Celery Redis broker ping failed")
'

# A synchronization validates Docling conversion and access to kbs_docs, mrag
# storage, mrag Qdrant, containerized Ollama, and bge-m3.
#
# Exit status alone is not enough: per-document conversion failures are a
# supported outcome that exits 0, so the terminal event decides the verdict.
# A system failure exits non-zero and is caught by `set -e` before we get here.
sync_log="$(mktemp)"
trap 'rm -f "$sync_log"' EXIT
"$root_dir/scripts/sync-now.sh" | tee "$sync_log"

sync_outcome="$(
  python3 - "$sync_log" <<'PYTHON'
import json
import sys

terminal = {
    "sync_completed",
    "sync_completed_with_errors",
    "sync_failed",
    "sync_skipped",
  "sync_no_changes",
}
last = None
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if event.get("event") in terminal:
        last = event
print(json.dumps(last) if last else "")
PYTHON
)"

if [[ -z "$sync_outcome" ]]; then
  echo "Synchronization produced no terminal event; treating as failure." >&2
  exit 1
fi

sync_event="$(printf '%s' "$sync_outcome" | python3 -c 'import json,sys; print(json.load(sys.stdin)["event"])')"
case "$sync_event" in
  sync_completed)
    ;;
  sync_no_changes)
    ;;
  sync_completed_with_errors)
    # A supported outcome: healthy documents synchronized while some were
    # degraded. Report it loudly, but do not fail verification.
    echo "WARNING: synchronization completed with document-level errors." >&2
    printf '%s\n' "$sync_outcome" >&2
    echo "Inspect them with: ./scripts/compose.sh exec --user mrag mrag-batch \\" >&2
    echo "  python /app/batch/docling_admin.py status --converted-dir /data/converted-docs" >&2
    ;;
  sync_skipped)
    echo "Synchronization was skipped by the exclusive lock, so nothing was verified." >&2
    exit 1
    ;;
  *)
    echo "Synchronization reported $sync_event." >&2
    exit 1
    ;;
esac

echo "Verification succeeded."
echo "Dify:     http://localhost/install"
echo "mrag API: http://localhost:${MRAG_EXPOSE_PORT:-8000}/docs"
