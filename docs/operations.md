# Operations

[日本語](./operations-ja.md)

## Status

```bash
./scripts/compose.sh ps
./scripts/compose.sh logs --tail=100 mrag-api mrag-batch
./scripts/docling-status.sh
```

Dify is available at <http://localhost/> and local mrag API documentation at
<http://localhost:8000/docs> unless ports were changed.

## Synchronization

Always inspect the plan first:

```bash
./scripts/sync-dry-run.sh
./scripts/sync-now.sh
```

The default schedule is every three hours in the configured timezone. A
non-blocking lock prevents overlapping scheduled and manual runs.

Structured log events include `sync_plan`, `document_added`,
`document_removed`, `sync_completed`, `sync_failed`, and
`deletion_guard_warning`.

## Deletion guard

By default, a run stops before deleting at least 20% or at least 50 documents.
Verify the source bind mount and dry-run output before overriding:

```bash
./scripts/sync-now.sh --allow-large-delete
```

## Reindex and reconvert

Reindex after changing embedding or chunking settings:

```bash
./scripts/reindex-now.sh
```

Reconvert after changing conversion behavior or to rebuild converted Markdown:

```bash
./scripts/sync-dry-run.sh
./scripts/reconvert-now.sh
```

## Model management

```bash
./scripts/ollama-model.sh list
./scripts/ollama-model.sh pull MODEL
./scripts/ollama-model.sh remove MODEL
```

Removal of a model referenced by a profile is refused unless explicitly forced.
Reindex after changing an embedding model.

## Backup and restore

Use the full-stack backup tools and verify the archive before restoring:

```bash
./scripts/backup.sh --stop-services
./scripts/restore.sh --archive PATH
```

Restore defaults to planning only. See [Backup and restore](./backup-and-restore.md)
before executing it.

## Updates

Update only through a reviewed change that modifies `VERSIONS`, image locks,
notices, and tests together. Preserve `.env`, profiles, source documents, and
volumes. Run `upgrade-env.sh`, rebuild, validate Compose, start, and verify.

## Troubleshooting

Use the [troubleshooting guide](./troubleshooting.md). Do not use volume deletion,
Docker prune, or `down -v` as routine recovery operations.
