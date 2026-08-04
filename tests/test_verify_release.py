from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "verify_release.py"
SPEC = importlib.util.spec_from_file_location("verify_release", MODULE_PATH)
assert SPEC and SPEC.loader
verify_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_release)


def test_japanese_partner() -> None:
    assert verify_release.japanese_partner(Path("docs/file.md")) == Path(
        "docs/file-ja.md"
    )


def test_compose_images_must_be_pinned() -> None:
    errors = verify_release.validate_compose_images(
        {
            "services": {
                "good": {"image": "example/app:1@sha256:abc"},
                "local": {"image": "local/app:1"},
                "bad": {"image": "example/app:latest"},
            }
        }
    )
    assert errors == [
        "service bad uses an image without a digest: example/app:latest"
    ]
