# First-Time Setup Runbook

[日本語](./SETUP-ja.md) · [Installation reference](./docs/installation.md) · [Troubleshooting](./docs/troubleshooting.md)

This runbook is the authoritative first-install sequence for both people and
automation agents. Follow it from top to bottom without reordering commands.
Setup is complete only when the verification command reports success.

The runbook is intentionally conservative. It preserves existing configuration
and data, stops on unexpected source revisions, and does not use destructive
Docker cleanup as a repair method.

## Safety contract

Before running any command:

1. Work from the repository root. All paths below are relative to that directory.
2. Stop when a command exits nonzero. Read its error before continuing.
3. Never overwrite an existing `.env`; it contains generated credentials.
4. Never run `docker compose down -v`, `docker volume rm`, or a system-wide
   Docker prune as part of setup or troubleshooting.
5. Do not stop unrelated containers to resolve a port conflict without the
   owner's approval. Change this stack's ports instead.
6. On a brand-new installation, leave `kbs_docs/` as cloned (`.gitkeep` only)
   until the base stack passes verification. On an existing installation,
   never remove its documents to follow this rule.
7. Do not print, paste into chat, commit, or upload `.env`, API keys, document
   contents, backups, or unredacted logs.
8. Do not expose a fresh Dify installation to an untrusted network. Create its
   administrator account first, then add HTTPS and access controls.

If this directory already contains a working installation or valuable Docker
volumes, stop: this first-install runbook is not an upgrade or reset procedure.
Take a backup and use the operations documentation instead. Continue here only
when deliberately resuming an incomplete first setup. See
[Backup and restore](./docs/backup-and-restore.md).

## 1. Confirm the host is suitable

Minimum host tools:

- Docker Engine or Docker Desktop with Docker Compose v2
- Git
- Python 3.11 or later
- `curl` on Linux and macOS
- Internet access for the initial source, image, package, and model downloads

An evaluation installation should start with approximately 8 CPU cores, 24 GiB
of host memory, and 60 GiB of free storage. The default Docling service limit
alone is 8 CPUs and 12 GiB. Production sizing depends on document complexity,
OCR, and concurrency; review [Supported platforms and sizing](./docs/supported-platforms.md).

### Linux and macOS preflight

```bash
docker version
docker compose version
docker ps
git --version
python3 --version
curl --version
docker compose ls
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

Do not continue if Docker is unreachable, Compose is v1, or Python is older than
3.11. Check whether ports 80, 443, and 8000 are already in use. An existing
Compose project named `mrag-dify` may be an earlier installation; do not take it
over without confirming ownership and backing it up.

Before `up -d` on a remote or cloud host, configure its firewall or security
group so ports 80 and 443 are reachable only from the trusted setup operator.
The Dify nginx port is published on the host; administrator creation must not be
left open to the internet.

On macOS, use the CPU profile. Docker cannot pass Apple Metal acceleration to
these containers.

### Native Windows preflight

Use Windows 10 or 11, Docker Desktop with the WSL2 backend, and Linux
containers. Put the repository on a local path shared with Docker Desktop, such
as `C:\src\mrag_dify_docker`. If it is stored inside the WSL filesystem, use the
Linux instructions from a WSL shell instead of mixing Windows and Linux scripts.

From PowerShell or Command Prompt:

```powershell
.\win-scripts\check-prerequisites.bat
docker compose ls
docker ps --format "table {{.Names}}\t{{.Ports}}"
```

The prerequisite script stops if Docker is unavailable or is running Windows
containers.

### NVIDIA preflight

NVIDIA acceleration is supported on Linux with NVIDIA Container Toolkit and on
Windows Docker Desktop with WSL2. Before selecting an NVIDIA profile, use the
NVIDIA Container Toolkit validation procedure appropriate for the installed
driver and confirm that a CUDA container can run `nvidia-smi`.

Do not select `nvidia` merely because host `nvidia-smi` works. Docker must also
be able to allocate the GPU. If container-level validation fails, keep both
accelerators set to `cpu` and fix the host runtime separately.

## 2. Obtain a clean checkout

For a new checkout:

```bash
git clone --recurse-submodules https://github.com/bathtimefish/mrag_dify_docker.git
cd mrag_dify_docker
```

If the repository is already cloned, change to its root and confirm that local
source edits will not affect setup:

```bash
git status --short
git submodule status
```

Do not discard local changes automatically. Ask their owner what to do if the
working tree contains unexpected modifications.

Download and validate the pinned upstream sources:

```bash
./scripts/bootstrap-vendors.sh
```

Native Windows:

```powershell
.\win-scripts\bootstrap-vendors.bat
```

The command prepares the pinned Dify checkout and mrag submodule. If it reports
an unexpected commit, stop. Do not bypass the check or edit files under
`vendor/dify`; use a fresh checkout or investigate why that directory differs.

## 3. Create and review local configuration

Use exactly one branch of this conditional command on Linux or macOS:

```bash
if [[ -f .env ]]; then
  ./scripts/upgrade-env.sh
else
  ./scripts/init-env.sh
fi
```

PowerShell on native Windows:

```powershell
if (Test-Path .env) {
    .\win-scripts\upgrade-env.bat
} else {
    .\win-scripts\init-env.bat
}
```

`init-env` generates random credentials. On Unix it also sets mode `0600`.
`upgrade-env` adds missing settings without changing existing values or secrets.
An `already exists` error from `init-env` is a safety stop, not a reason to
delete `.env`; run `upgrade-env` instead.

Review `.env` locally without copying its contents into an issue or chat. The
CPU defaults work on every supported host. Change only settings you understand:

| Setting | Safe initial value | Notes |
|---|---|---|
| `COMPOSE_PROJECT_NAME` | `mrag-dify` | Must be unique if multiple installations share a Docker host |
| `OLLAMA_ACCELERATOR` | `cpu` | Set to `nvidia` only after container-level GPU validation |
| `DOCLING_ACCELERATOR` | `cpu` | Independent of the Ollama setting |
| `MODEL_DOWNLOAD_OFFLINE` | `false` | A fresh installation needs model downloads |
| `EXPOSE_NGINX_PORT` | `80` | Change if another owned service must keep port 80 |
| `EXPOSE_NGINX_SSL_PORT` | `443` | Change if port 443 is already reserved |
| `MRAG_EXPOSE_PORT` | `8000` | Bound to loopback by default |
| `DOCLING_CPUS` | `8.0` | Reduce only when the host cannot provide the default |
| `DOCLING_MEMORY_LIMIT` | `12g` | Lower values may cause conversion failures |

If ports conflict, choose unused numeric ports in `.env` before the first
`up -d`. Do not stop an unrelated stack. The setup verification reads the
configured HTTP and mrag ports.

For NVIDIA, set either or both accelerator variables to `nvidia`. They are
independent. macOS must keep both at `cpu`.

## 4. Build the pinned custom images

Linux and macOS:

```bash
./scripts/build.sh
```

Native Windows:

```powershell
.\win-scripts\build.bat
```

The build validates the mrag gitlink, source commit, package version, CPU
PyTorch pin, and sqlite-vaporetto checksum before producing
`local/mrag-runtime:1.0.0` and `local/mrag-batch:1.0.0`.

If a download fails, confirm DNS, proxy settings, registry access, and free
storage, then rerun the same build command. Docker reuses completed layers. Do
not delete volumes or alter pinned checksums to make a failed download pass.
Repeated Docker Hub rate-limit errors require registry authentication or waiting
for the limit window to reset.

## 5. Validate Compose before starting

Linux and macOS:

```bash
./scripts/compose.sh config --quiet
```

Native Windows:

```powershell
.\win-scripts\compose.bat config --quiet
```

Do not continue if this command fails. Common causes are a missing `.env`, an
incomplete Dify checkout, an invalid accelerator value, or a malformed local
setting. Fix the reported input and rerun validation; do not edit the generated
`vendor/dify/docker/.env` file.

## 6. Start and verify the complete stack

Linux and macOS:

```bash
./scripts/compose.sh up -d
VERIFY_TIMEOUT_SECONDS=900 ./scripts/verify.sh
```

Native Windows PowerShell:

```powershell
.\win-scripts\compose.bat up -d
$env:VERIFY_TIMEOUT_SECONDS = "900"
.\win-scripts\verify.bat
Remove-Item Env:VERIFY_TIMEOUT_SECONDS
```

The first start downloads the pinned Ollama embedding model and reranker model.
It may take several minutes. Keep the terminal and network available. A retry
must use the same `up -d` and verification commands; do not rerun `init-env` and
do not delete the model or data volumes.

Verification checks all long-running services, one-shot initializers, health
checks, Dify and mrag HTTP endpoints, Docling authentication and a smoke
conversion, reranker loading, Redis authentication, Dify-to-mrag connectivity,
Ollama embedding generation, Qdrant access, and an empty or real document sync.

If verification fails, collect read-only state first:

```bash
./scripts/compose.sh ps -a
./scripts/compose.sh logs --tail=200 ollama ollama-model-init mrag-init mrag-api
./scripts/compose.sh logs --tail=200 docling-serve mrag-batch api worker nginx
./scripts/docling-doctor.sh
./scripts/mrag-doctor.sh
```

Native Windows uses the same commands through `win-scripts\compose.bat`, plus
`docling-doctor.bat` and `mrag-doctor.bat`.

Before sharing logs, redact credentials, document names and contents, hostnames,
and public addresses. Use the following response for common failures:

| Symptom | Safe response |
|---|---|
| Service remains `starting` | Check memory, disk space, and that the 900-second window was sufficient; inspect that service's logs |
| `ollama-model-init` fails | Confirm outbound DNS/HTTPS and registry access; preserve `ollama_models` and retry |
| `mrag-init` fails | Inspect Ollama and reranker download errors; preserve `mrag_data`, profiles, and model cache |
| Port allocation fails | Change this project's port in `.env`; do not stop an unrelated stack |
| Docling smoke conversion fails | Check Docling logs, memory pressure, API health, and CPU/GPU selection |
| Sync reports a deletion guard | Stop and verify the `kbs_docs/` mount; never bypass it during unexplained data loss |
| Dify endpoint loads indefinitely | Restart only nginx with `./scripts/compose.sh restart nginx`, then rerun verification |
| Docker reports NVIDIA but VRAM is unused | Treat it as CPU fallback and complete the GPU checks below |

Do not report success while verification is failing, skipped by a sync lock, or
still waiting for a required model.

### Verify NVIDIA use

If `OLLAMA_ACCELERATOR=nvidia`:

```bash
./scripts/verify-ollama-gpu.sh
```

Native Windows:

```powershell
.\win-scripts\verify-ollama-gpu.bat
```

This command performs an embedding request and fails unless Ollama reports
nonzero VRAM use. A healthy HTTP response alone is not proof of GPU execution.

If `DOCLING_ACCELERATOR=nvidia`, also run `docling-doctor` and inspect the
Docling service logs for CUDA initialization or fallback messages. If GPU
allocation is uncertain, use the CPU profile until the host runtime is fixed.

## 7. Complete the user-owned Dify configuration

With default ports:

- Dify installation: <http://localhost/install>
- mrag API documentation: <http://localhost:8000/docs>

If ports were changed, substitute `EXPOSE_NGINX_PORT` and `MRAG_EXPOSE_PORT`
from `.env` in those URLs.

The user must perform these steps in the Dify interface:

1. Create the first Dify administrator from a trusted host.
2. Configure an answer-generation provider supported by Dify. Provider API keys
   belong in Dify's credential store and must not be given to the setup agent.
3. Add an External Knowledge source with:

   | Setting | Value |
   |---|---|
   | Endpoint | `http://mrag-api:8000` |
   | API key | the local `MRAG_API_KEY` value from `.env` |
   | Knowledge ID | `main_kb` |

   Do not append `/retrieval`; Dify adds it automatically.
4. Import or create a Chat Workflow containing Knowledge Retrieval.

Complete administrator creation and HTTPS/network controls before making Dify
reachable from an untrusted network.

## 8. Add documents only after base verification

Place supported files in `kbs_docs/`, then preview and apply the change:

```bash
./scripts/sync-dry-run.sh
./scripts/sync-now.sh
```

Supported inputs are PDF, Markdown, plain text, Word, Excel, PowerPoint, and the
image extensions listed in the README. Start with a small representative set.
Review conversion status before adding a large corpus:

```bash
./scripts/docling-status.sh
```

If a document fails, the system preserves the last successful converted copy
and reports the error. Do not disable page, size, timeout, or deletion guards
without understanding the affected corpus.

## Completion criteria

Setup may be reported as complete only when:

- the custom image build succeeded;
- Compose configuration validation succeeded;
- `verify.sh` or `verify.bat` printed `Verification succeeded.`;
- NVIDIA Ollama verification succeeded when that profile was selected;
- the operator received the Dify and mrag URLs;
- remaining user-owned work—Dify administrator creation, provider credentials,
  workflow creation, HTTPS, and production access control—was stated clearly.

For later operation, backup, recovery, and detailed failure handling, continue
with [Operations](./docs/operations.md),
[Backup and restore](./docs/backup-and-restore.md), and
[Troubleshooting](./docs/troubleshooting.md).
