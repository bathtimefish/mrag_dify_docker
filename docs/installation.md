# Installation

[日本語](./installation-ja.md) · [Back to README](../README.md)

## 1. Choose a profile

Set `OLLAMA_ACCELERATOR` and `DOCLING_ACCELERATOR` independently in `.env`.

| Value | Use when |
|---|---|
| `cpu` | Portable default, macOS, or a host without an NVIDIA runtime |
| `nvidia` | Linux or Windows Docker Desktop with a working NVIDIA container runtime |

Confirm Docker before setup:

```bash
docker version
docker compose version
docker ps
git --version
python3 --version
```

## 2. Download pinned upstream sources

```bash
./scripts/bootstrap-vendors.sh
```

The command checks out the Dify commit recorded in `VERSIONS` and initializes
the pinned mrag submodule. It stops if either source revision is unexpected.

## 3. Create local configuration

```bash
./scripts/init-env.sh
```

This creates `.env` with random credentials and mode `0600` on Unix. Never
commit or share this file. If `.env` already exists, preserve it and add only
missing settings:

```bash
./scripts/upgrade-env.sh
```

Review resource limits, timezone, schedule, ports, and accelerator settings.

## 4. Build and start

```bash
./scripts/build.sh
./scripts/compose.sh config --quiet
./scripts/compose.sh up -d
./scripts/verify.sh
```

The first start downloads the embedding and reranker models. It can take a long
time on a slow connection. Do not delete Docker volumes between retries.

## 5. Complete Dify setup

1. Open <http://localhost/install> from the deployment host.
2. Create the administrator account before allowing untrusted network access.
3. Configure any Dify-supported answer-generation provider.
4. Add mrag as External Knowledge using the values in the README.
5. Import or create a Chat Workflow using Knowledge Retrieval.

Enable HTTPS before exposing Dify beyond a trusted local network.

## Native Windows

Use the `.bat` launchers from PowerShell or Command Prompt:

```powershell
.\win-scripts\check-prerequisites.bat
.\win-scripts\bootstrap-vendors.bat
.\win-scripts\init-env.bat
.\win-scripts\build.bat
.\win-scripts\compose.bat config --quiet
.\win-scripts\compose.bat up -d
.\win-scripts\verify.bat
```

Store the repository on a local Windows path shared with Docker Desktop. If the
repository is inside the WSL filesystem, use the Unix scripts from WSL instead.

## NVIDIA verification

Verify that a test CUDA container can run `nvidia-smi` before selecting an
NVIDIA profile. After startup:

```bash
./scripts/verify-ollama-gpu.sh
./scripts/docling-doctor.sh
```

On native Windows use `verify-ollama-gpu.bat` and `docling-doctor.bat`.

## Stop or remove containers

```bash
./scripts/compose.sh stop
./scripts/compose.sh up -d
```

`down` removes containers and networks but preserves volumes. Do not use
`down -v` unless permanent deletion of Dify data, indexes, models, and backups
is explicitly intended.
