# Troubleshooting

[日本語](./troubleshooting-ja.md)

Start with read-only diagnostics:

```bash
./scripts/compose.sh ps
./scripts/compose.sh logs --tail=100 mrag-api mrag-batch docling-serve ollama
./scripts/docling-doctor.sh
./scripts/mrag-doctor.sh
./scripts/sync-dry-run.sh
```

Redact credentials, document names, hostnames, public IPs, and document content
before sharing output.

## A service does not start

```bash
./scripts/compose.sh config --quiet
./scripts/compose.sh ps -a
./scripts/compose.sh logs --tail=200 SERVICE
```

Check disk space, port conflicts, Docker memory, image download failures, and
health-check output. Do not run `down -v` as a repair step.

## Model initialization fails

```bash
./scripts/compose.sh ps ollama ollama-model-init
./scripts/compose.sh logs --tail=200 ollama ollama-model-init
./scripts/compose.sh exec ollama ollama list
```

Confirm DNS, registry access, free storage, and the expected model identifier.
Keep the model volume when retrying.

## Documents are not indexed

```bash
./scripts/docling-status.sh
./scripts/sync-dry-run.sh
./scripts/sync-now.sh
```

Look for zero-byte files, files changing during the scan, unsupported formats,
conversion failures, timeout budgets, and deletion-guard messages. Do not bypass
the deletion guard until the source mount has been verified.

## Dify cannot reach mrag

Confirm:

- Endpoint is `http://mrag-api:8000` without `/retrieval`;
- Knowledge ID is `main_kb`;
- the API key matches `MRAG_API_KEY`;
- `SSRF_PROXY_ALLOW_PRIVATE_DOMAINS` contains `mrag-api`;
- `mrag-api` is healthy.

## NVIDIA is not used

Verify Docker can run a CUDA test image, check accelerator values in `.env`, and
run the GPU verification commands in the installation guide. A successful API
response alone does not prove that a model is loaded into VRAM.
