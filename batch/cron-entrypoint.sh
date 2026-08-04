#!/usr/bin/env bash
set -euo pipefail

schedule="${MRAG_CRON_SCHEDULE:-0 */3 * * *}"
if [[ "$schedule" == *$'\n'* ]]; then
  echo "MRAG_CRON_SCHEDULE must not contain newlines" >&2
  exit 2
fi

chown -R mrag:mrag \
  "${MRAG_PROJECT_DIR:-/data/mrag}" \
  "${MRAG_BACKUP_DIR:-/backups}"

cat >/etc/cron.d/mrag-sync <<EOF
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
TZ=${TZ:-Asia/Tokyo}
MRAG_PROJECT_DIR=${MRAG_PROJECT_DIR:-/data/mrag}
MRAG_SOURCE_DIR=${MRAG_SOURCE_DIR:-/kbs_docs}
MRAG_BACKUP_DIR=${MRAG_BACKUP_DIR:-/backups}
MRAG_DELETE_MAX_PERCENT=${MRAG_DELETE_MAX_PERCENT:-20}
MRAG_DELETE_MAX_COUNT=${MRAG_DELETE_MAX_COUNT:-50}
MRAG_BACKUP_RETENTION=${MRAG_BACKUP_RETENTION:-7}
MRAG_OLLAMA_URL=${MRAG_OLLAMA_URL:-http://ollama:11434}
MRAG_EMBEDDING_MODEL=${MRAG_EMBEDDING_MODEL:-bge-m3:latest}
MRAG_QDRANT_HOST=${MRAG_QDRANT_HOST:-mrag-qdrant}
MRAG_QDRANT_PORT=${MRAG_QDRANT_PORT:-6333}
# cron's per-user shell cannot open PID 1's root-owned file descriptors when
# running as mrag. Let cron's root shell open the Docker log descriptors, then
# drop privileges for the synchronization process itself.
${schedule} root su -s /bin/bash mrag -c /app/batch/run-sync.sh >>/proc/1/fd/1 2>>/proc/1/fd/2
EOF
chmod 0644 /etc/cron.d/mrag-sync

echo "mrag sync scheduler started: ${schedule} (${TZ:-Asia/Tokyo})"
exec cron -f
