# Docling Ingestion Commands

[日本語](./docling-ingestion-operations-ja.md)

| Unix | Windows | Purpose |
|---|---|---|
| `./scripts/sync-dry-run.sh` | `.\win-scripts\sync-dry-run.bat` | Show the conversion and synchronization plan |
| `./scripts/sync-now.sh` | `.\win-scripts\sync-now.bat` | Convert and synchronize |
| `./scripts/convert-only.sh` | `.\win-scripts\convert-only.bat` | Convert without modifying mrag |
| `./scripts/reconvert-now.sh` | `.\win-scripts\reconvert-now.bat` | Force all conversions and synchronize |
| `./scripts/docling-status.sh` | `.\win-scripts\docling-status.bat` | Show manifest and unresolved errors |
| `./scripts/docling-doctor.sh` | `.\win-scripts\docling-doctor.bat` | Validate effective configuration and connectivity |

All modifying commands use the same synchronization lock.

## Diagnostics

```bash
./scripts/docling-doctor.sh
./scripts/docling-doctor.sh --no-probe
./scripts/docling-status.sh
```

`--no-probe` prints effective settings without network access. Status returns a
non-zero exit code for missing, corrupt, or incompatible manifests and does not
attempt automatic repair.

## Conversion-only inspection

```bash
./scripts/convert-only.sh
./scripts/docling-status.sh
```

This updates converted Markdown and the conversion manifest without running
`mrag add`, `mrag remove`, or `mrag index`.

## Forced reconversion

```bash
./scripts/sync-dry-run.sh
./scripts/reconvert-now.sh
```

Forced reconversion does not expand the deletion plan. Deletions are based only
on source files that are actually absent.

## Staging cleanup

The administration command defaults to dry-run:

```bash
python3 batch/docling_admin.py cleanup-staging \
  --converted-dir /path/to/converted-docs \
  --older-than-seconds 86400
```

Use `--execute` only after verifying no conversion or synchronization process is
running. The command deletes only staging runs older than the selected age.
