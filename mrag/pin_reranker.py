#!/usr/bin/env python3
from __future__ import annotations

import os

from huggingface_hub import snapshot_download


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false; got {raw!r}")


repository = os.environ.get(
    "MRAG_RERANK_MODEL",
    "hotchpotch/japanese-reranker-cross-encoder-small-v1",
)
revision = os.environ.get(
    "MRAG_RERANK_REVISION",
    "d7462bce0b065e0624028b412693dec055d719a0",
)
path = snapshot_download(
    repo_id=repository,
    revision=revision,
    local_files_only=env_bool("MODEL_DOWNLOAD_OFFLINE", False),
)
print(path)
