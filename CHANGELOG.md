# Changelog

[日本語](./CHANGELOG-ja.md)

All notable changes are documented here. This project follows
[Semantic Versioning](https://semver.org/).

## Unreleased

- Fixed synchronization failing with `PermissionError` on a clean install: the
  batch entrypoint now hands the `mrag_converted_docs` volume to the `mrag` user
  along with the project and backup volumes.
- Stopped appending a second `.md` to sources that are already Markdown, so
  citations show `report.md` instead of `report.md.md`. The next synchronization
  renames existing converted Markdown; documents already indexed keep their old
  title until their source changes, because the index is keyed by content. A
  source whose converted name another source already claims is now skipped and
  reported as `output_path_conflict` instead of racing for the same file.

## 1.0.1 - 2026-08-04

- Added a bilingual first-time setup runbook for people and automation agents.
- Made verification honor the configured Dify and mrag host ports.

## 1.0.0 - 2026-08-04

Initial public release.

- Integrated Dify, mrag, Docling Serve, Ollama, and dedicated Qdrant services.
- Added PDF, text, Office, and image ingestion through Docling.
- Added guarded incremental synchronization, dry runs, diagnostics, backup, and recovery tools.
- Added CPU and NVIDIA profiles with Linux, macOS, and Windows launchers.
- Added Japanese full-text retrieval and cross-encoder reranking.
