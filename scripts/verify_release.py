#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

PUBLIC_DOCUMENTS = (
    "README.md",
    "SETUP.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "docs/architecture.md",
    "docs/backup-and-restore.md",
    "docs/docling-ingestion-operations.md",
    "docs/installation.md",
    "docs/operations.md",
    "docs/privacy.md",
    "docs/security.md",
    "docs/supported-platforms.md",
    "docs/troubleshooting.md",
)


def load_versions(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def japanese_partner(path: Path) -> Path:
    return path.with_name(f"{path.stem}-ja{path.suffix}")


def validate_documents(root: Path) -> list[str]:
    errors: list[str] = []
    for relative in PUBLIC_DOCUMENTS:
        english = root / relative
        japanese = japanese_partner(english)
        if not english.is_file():
            errors.append(f"missing public English document: {relative}")
        if not japanese.is_file():
            errors.append(
                f"missing Japanese document: {japanese.relative_to(root).as_posix()}"
            )
    return errors


def validate_compose_images(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    services = config.get("services", {})
    if not isinstance(services, dict):
        return ["Compose configuration has no services mapping"]
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        image = service.get("image")
        if not isinstance(image, str) or image.startswith("local/"):
            continue
        if "@sha256:" not in image:
            errors.append(f"service {name} uses an image without a digest: {image}")
    return errors


def validate_versions(root: Path, versions: dict[str, str]) -> list[str]:
    errors: list[str] = []
    required = {
        "PROJECT_VERSION",
        "DIFY_COMMIT",
        "MRAG_COMMIT",
        "OLLAMA_IMAGE",
        "OLLAMA_EMBEDDING_MODEL_MANIFEST_SHA256",
        "RERANK_MODEL_REVISION",
        "MRAG_QDRANT_IMAGE",
        "BACKUP_HELPER_IMAGE",
        "PYTORCH_VERSION",
    }
    for key in sorted(required):
        if not versions.get(key):
            errors.append(f"VERSIONS is missing {key}")
    if versions.get("PROJECT_VERSION") != "1.0.0":
        errors.append("PROJECT_VERSION must be 1.0.0 for this release")
    for key in ("OLLAMA_IMAGE", "MRAG_QDRANT_IMAGE"):
        value = versions.get(key, "")
        if "@sha256:" not in value:
            errors.append(f"{key} is not digest-pinned")
    helper = versions.get("BACKUP_HELPER_IMAGE", "")
    if not helper.startswith("local/mrag-runtime:"):
        errors.append("BACKUP_HELPER_IMAGE must use the locally built pinned runtime")
    for key in ("DIFY_COMMIT", "MRAG_COMMIT", "RERANK_MODEL_REVISION"):
        value = versions.get(key, "")
        if value and not re.fullmatch(r"[0-9a-f]{40}", value):
            errors.append(f"{key} is not a full 40-character commit")
    manifest = versions.get("OLLAMA_EMBEDDING_MODEL_MANIFEST_SHA256", "")
    if manifest and not re.fullmatch(r"[0-9a-f]{64}", manifest):
        errors.append("OLLAMA_EMBEDDING_MODEL_MANIFEST_SHA256 is invalid")
    constraints = (root / "constraints-runtime.txt").read_text(encoding="utf-8")
    expected_torch = f"torch=={versions.get('PYTORCH_VERSION', '')}"
    if expected_torch not in constraints:
        errors.append(f"runtime constraints do not contain {expected_torch}")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--compose-json", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    config = json.loads(args.compose_json.read_text(encoding="utf-8"))
    versions = load_versions(root / "VERSIONS")
    errors = [
        *validate_documents(root),
        *validate_compose_images(config),
        *validate_versions(root, versions),
    ]
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("Release metadata, bilingual documentation, and image locks are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
