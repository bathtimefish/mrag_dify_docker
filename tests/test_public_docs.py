from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "scripts" / "verify_release.py"
SPEC = importlib.util.spec_from_file_location("verify_release_docs", MODULE_PATH)
assert SPEC and SPEC.loader
verify_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_release)
MARKDOWN_LINK = re.compile(r"\[[^]]*\]\(([^)]+)\)")


def test_public_document_links_resolve() -> None:
    failures: list[str] = []
    for relative in verify_release.PUBLIC_DOCUMENTS:
        for document in (ROOT / relative, verify_release.japanese_partner(ROOT / relative)):
            body = document.read_text(encoding="utf-8")
            for match in MARKDOWN_LINK.finditer(body):
                target = match.group(1).strip().split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (document.parent / unquote(target)).resolve()
                if not resolved.exists():
                    failures.append(
                        f"{document.relative_to(ROOT)} -> {target} does not exist"
                    )
    assert failures == []


def test_private_development_documents_are_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    required = {
        "/.private-docs/",
        "/TODO.md",
        "/specs/",
        "/docs/docling-ingestion-spec.md",
        "/docs/docling-ingestion-todo.md",
    }
    assert required.issubset(set(ignore.splitlines()))
    assert "/SETUP.md" not in set(ignore.splitlines())
