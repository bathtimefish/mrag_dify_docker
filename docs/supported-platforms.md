# Supported Platforms and Sizing

[日本語](./supported-platforms-ja.md)

## Platform matrix

| Host | CPU profile | NVIDIA profile | Notes |
|---|---:|---:|---|
| Linux x86-64 | Supported | Supported | NVIDIA Container Toolkit is required for GPU use |
| Linux arm64 | Supported | Not part of the release test matrix | sqlite-vaporetto provides an arm64 artifact |
| Windows 10/11 with Docker Desktop | Supported | Supported | Use Linux containers and the WSL2 backend |
| macOS Apple Silicon or Intel | Supported | Not available | Docker cannot pass Apple Metal to these containers |

## Resource planning

Workload size, document complexity, OCR, and concurrency have a large effect.
Treat these as starting points and measure the intended corpus before production.

| Profile | Host CPU | Host memory | GPU | Suggested free storage |
|---|---:|---:|---:|---:|
| Evaluation CPU | 8 cores | 24 GiB | None | 60 GiB |
| Production CPU starting point | 12+ cores | 32+ GiB | None | 100+ GiB |
| NVIDIA starting point | 8+ cores | 32+ GiB | 16+ GiB VRAM | 100+ GiB |

The default Docling limit is 8 CPUs and 12 GiB. Dify, PostgreSQL, two Qdrant
instances, Redis, Ollama, model caches, converted Markdown, backups, and source
documents require additional capacity.

## Capacity checks

Before production, measure:

- first and incremental conversion duration;
- peak CPU, RAM, and VRAM during conversion and concurrent search;
- model cache, converted document, index, database, and backup sizes;
- normal and worst-case document page counts;
- whether a scheduled run finishes before the next run begins.

Set `DOCLING_CPUS`, `DOCLING_MEMORY_LIMIT`, `DOCLING_WORKERS`, and
`DOCLING_BATCH_CONCURRENCY` from measured results. Raising concurrency without
measuring memory can cause host pressure or GPU out-of-memory failures.
