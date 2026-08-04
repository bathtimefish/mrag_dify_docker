#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

import yaml


def load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream)
    return value if isinstance(value, dict) else {}


def save_yaml(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        yaml.safe_dump(value, stream, allow_unicode=True, sort_keys=False)
    temporary.replace(path)


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{name} must be one of true/false, 1/0, yes/no, or on/off; got {raw!r}"
    )


project_dir = Path(os.environ.get("MRAG_PROJECT_DIR", "/data/mrag"))
project_config_path = project_dir / "mrag.yaml"
profile_path = project_dir / "profiles" / "default.yaml"

project = load_yaml(project_config_path)
project["project"] = {"name": os.environ.get("MRAG_PROJECT_NAME", "main-kb")}
project["knowledge_base"] = {
    "id": os.environ.get("MRAG_KNOWLEDGE_ID", "main_kb"),
    "name": os.environ.get("MRAG_PROJECT_NAME", "main-kb"),
}
project["default_profile"] = "default"
project["qdrant"] = {
    "mode": "server",
    "host": os.environ.get("MRAG_QDRANT_HOST", "mrag-qdrant"),
    "port": int(os.environ.get("MRAG_QDRANT_PORT", "6333")),
}
save_yaml(project_config_path, project)

if not env_bool("MRAG_PRESERVE_EXISTING_PROFILE", False):
    profile = load_yaml(profile_path)
    profile["embedding"] = {
        **profile.get("embedding", {}),
        "provider": "ollama",
        "model": os.environ.get("MRAG_EMBEDDING_MODEL", "bge-m3:latest"),
        "endpoint": os.environ.get(
            "MRAG_OLLAMA_URL", "http://ollama:11434"
        ),
    }
    profile.setdefault("retrieval", {})["strategy"] = "hybrid"
    rerank = profile.setdefault("rerank", {})
    rerank["enabled"] = env_bool("MRAG_RERANK_ENABLED", True)
    rerank["provider"] = "sentence-transformers"
    rerank["model"] = os.environ.get(
        "MRAG_RERANK_MODEL",
        "hotchpotch/japanese-reranker-cross-encoder-small-v1",
    )
    rerank["max_length"] = int(os.environ.get("MRAG_RERANK_MAX_LENGTH", "512"))
    rerank["top_n"] = int(os.environ.get("MRAG_RERANK_TOP_N", "30"))
    save_yaml(profile_path, profile)
