from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "upgrade_env",
    ROOT / "scripts" / "upgrade-env.py",
)
assert SPEC and SPEC.loader
upgrade_env = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = upgrade_env
SPEC.loader.exec_module(upgrade_env)


def test_adds_missing_values_and_preserves_existing_file(tmp_path: Path) -> None:
    template = tmp_path / "template"
    destination = tmp_path / ".env"
    template.write_text(
        "CUSTOM=template\n"
        "DOCLING_SERVE_API_KEY=CHANGE_ME\n"
        "DOCLING_WORKERS=1\n",
        encoding="utf-8",
    )
    destination.write_text(
        "# user comment\nCUSTOM=user-value\n",
        encoding="utf-8",
    )
    destination.chmod(0o600)

    added = upgrade_env.add_missing(template, destination)
    content = destination.read_text(encoding="utf-8")

    assert added == ("DOCLING_SERVE_API_KEY", "DOCLING_WORKERS")
    assert "# user comment\nCUSTOM=user-value\n" in content
    assert "CUSTOM=template" not in content
    assert "DOCLING_SERVE_API_KEY=CHANGE_ME" not in content
    assert "DOCLING_SERVE_API_KEY=" in content
    assert "DOCLING_WORKERS=1" in content
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_second_run_is_idempotent(tmp_path: Path) -> None:
    template = tmp_path / "template"
    destination = tmp_path / ".env"
    template.write_text("NEW_SETTING=value\n", encoding="utf-8")
    destination.write_text("EXISTING=custom\n", encoding="utf-8")

    assert upgrade_env.add_missing(template, destination) == ("NEW_SETTING",)
    first = destination.read_bytes()
    assert upgrade_env.add_missing(template, destination) == ()

    assert destination.read_bytes() == first


def test_missing_destination_is_rejected(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.write_text("SETTING=value\n", encoding="utf-8")

    try:
        upgrade_env.add_missing(template, tmp_path / "missing")
    except FileNotFoundError as error:
        assert "run init-env first" in str(error)
    else:
        raise AssertionError("missing destination must fail")
