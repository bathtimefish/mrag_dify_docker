# Backup and Restore

[日本語](./backup-and-restore-ja.md)

The backup tool creates a consistent cold archive of the configured stack. It
stores credentials and operational data, so output files are created with mode
`0600` on Unix and must be encrypted and access-controlled by the operator.

## What is included

Default backup:

- root `.env`;
- host `mrag_profiles/`;
- Dify's bind-mounted `vendor/dify/docker/volumes/` data;
- `mrag_data`, `mrag_qdrant_data`, `mrag_converted_docs`, and `mrag_backups`
  Docker volumes;
- a JSON manifest with versions, volume names, and SHA-256 checksums.

Source documents are excluded unless `--include-documents` is specified.
Downloadable Ollama, reranker, and Docling model caches are excluded unless
`--include-caches` is specified.

## Create a backup

```bash
./scripts/backup.sh --stop-services
```

The command refuses to run while services are active unless `--stop-services`
is present. Services that were stopped by the command are restarted afterward
with the existing containers; backup does not pull images or apply new Compose
configuration.

Include source documents and model caches when required:

```bash
./scripts/backup.sh --stop-services --include-documents --include-caches
```

Use `--output /secure/path` to store the archive outside the repository.

## Validate and plan a restore

Restore is a dry run by default:

```bash
./scripts/restore.sh --archive /secure/path/ARCHIVE.tar.gz
```

It safely extracts metadata, rejects path traversal and links, verifies every
payload checksum, and prints target volumes and host data without modifying the
stack.

## Execute a restore

Run the plan first. Then provide the exact Compose project name printed by the
plan:

```bash
./scripts/restore.sh \
  --archive /secure/path/ARCHIVE.tar.gz \
  --execute \
  --stop-services \
  --confirm-project mrag-dify
```

Before overwriting anything, the tool automatically creates a `pre-restore`
safety backup beside the selected archive. It refuses an archive created for a
different Compose project name.

Add `--restore-documents` or `--restore-secrets` only when those items should
replace the current host data. Without those flags, `kbs_docs/` and `.env` are
left unchanged.

## After restoration

```bash
./scripts/verify.sh
./scripts/docling-status.sh
./scripts/sync-dry-run.sh
```

Run a representative search from Dify. If caches were excluded, allow the
services to download the pinned models again. Retain the pre-restore backup until
data counts and search results are accepted.

## Windows

Use `win-scripts\backup.bat` and `win-scripts\restore.bat` with the same options.
