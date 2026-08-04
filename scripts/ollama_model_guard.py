#!/usr/bin/env python3
"""Print the Ollama models that mrag profiles reference.

`ollama-model.sh` / `ollama-model.ps1` use this list to refuse removing a model
the running system depends on (the embedding model, or the augmentation model
when augmentation is enabled).

Only the small, deterministic subset of YAML that mrag writes (2-space indent,
top-level ``embedding:`` / ``augmentation:`` blocks) is parsed, so no third-party
YAML dependency is required on the host Python.
"""
from __future__ import annotations

import sys
from pathlib import Path


def normalize(model: str) -> str:
    """Return the model reference with an explicit tag so ``name`` and
    ``name:latest`` compare equal (Ollama treats them as the same model)."""
    model = model.strip().strip('"').strip("'")
    if not model:
        return ""
    return model if ":" in model else f"{model}:latest"


def scan_profile(text: str) -> set[str]:
    models: set[str] = set()
    section: str | None = None
    aug_strategy = "none"
    aug_model: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            section = line.split(":", 1)[0].strip()
            continue
        stripped = line.strip()
        if ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if section == "embedding" and key == "model" and value:
            models.add(normalize(value))
        elif section == "augmentation":
            if key == "strategy":
                aug_strategy = value.lower() or "none"
            elif key == "model":
                aug_model = value

    if aug_strategy not in ("", "none") and aug_model:
        models.add(normalize(aug_model))
    return models


def from_profiles(profiles_dir: Path) -> set[str]:
    models: set[str] = set()
    if not profiles_dir.is_dir():
        return models
    for path in sorted(profiles_dir.glob("*.yaml")):
        try:
            models |= scan_profile(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return models


def from_env(env_file: Path) -> set[str]:
    """Protect the embedding model even when profiles are unavailable."""
    models: set[str] = set()
    if not env_file.is_file():
        return models
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("MRAG_EMBEDDING_MODEL="):
            value = line.split("=", 1)[1].strip()
            if value:
                models.add(normalize(value))
    return models


def main() -> int:
    profiles_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("mrag_profiles")
    env_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".env")
    protected = from_profiles(profiles_dir) | from_env(env_file)
    for model in sorted(m for m in protected if m):
        print(model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
