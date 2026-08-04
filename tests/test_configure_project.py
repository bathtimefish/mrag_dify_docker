from __future__ import annotations

import runpy
from pathlib import Path

import yaml

SCRIPT = Path(__file__).parents[1] / "mrag" / "configure_project.py"


def prepare_project(path: Path) -> None:
    (path / "profiles").mkdir(parents=True)
    (path / "mrag.yaml").write_text("{}\n", encoding="utf-8")
    (path / "profiles" / "default.yaml").write_text(
        "name: default\n",
        encoding="utf-8",
    )


def test_reranking_is_enabled_by_default(tmp_path, monkeypatch) -> None:
    prepare_project(tmp_path)
    monkeypatch.setenv("MRAG_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("MRAG_RERANK_ENABLED", raising=False)

    runpy.run_path(str(SCRIPT))

    profile = yaml.safe_load(
        (tmp_path / "profiles" / "default.yaml").read_text(encoding="utf-8")
    )
    assert profile["rerank"] == {
        "enabled": True,
        "provider": "sentence-transformers",
        "model": "hotchpotch/japanese-reranker-cross-encoder-small-v1",
        "max_length": 512,
        "top_n": 30,
    }
    assert profile["embedding"]["endpoint"] == "http://ollama:11434"


def test_reranking_can_be_disabled(tmp_path, monkeypatch) -> None:
    prepare_project(tmp_path)
    monkeypatch.setenv("MRAG_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("MRAG_RERANK_ENABLED", "false")

    runpy.run_path(str(SCRIPT))

    profile = yaml.safe_load(
        (tmp_path / "profiles" / "default.yaml").read_text(encoding="utf-8")
    )
    assert profile["rerank"]["enabled"] is False


def test_existing_profile_can_be_preserved_exactly(tmp_path, monkeypatch) -> None:
    prepare_project(tmp_path)
    profile_path = tmp_path / "profiles" / "default.yaml"
    custom_profile = (
        "name: default\n"
        "retrieval:\n"
        "  strategy: vector\n"
        "embedding:\n"
        "  provider: custom\n"
        "  model: custom-model\n"
    )
    profile_path.write_text(custom_profile, encoding="utf-8")
    monkeypatch.setenv("MRAG_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("MRAG_PRESERVE_EXISTING_PROFILE", "true")

    runpy.run_path(str(SCRIPT))

    assert profile_path.read_text(encoding="utf-8") == custom_profile
