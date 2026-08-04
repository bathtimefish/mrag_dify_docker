from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "scripts" / "stack_backup.py"
SPEC = importlib.util.spec_from_file_location("stack_backup", MODULE_PATH)
assert SPEC and SPEC.loader
stack_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stack_backup)


def test_load_versions_ignores_comments(tmp_path: Path) -> None:
    (tmp_path / "VERSIONS").write_text(
        "# comment\nPROJECT_VERSION=1.0.0\nEMPTY=\n\n", encoding="utf-8"
    )
    assert stack_backup.load_versions(tmp_path) == {
        "PROJECT_VERSION": "1.0.0",
        "EMPTY": "",
    }


def test_configured_volume_names_uses_resolved_names() -> None:
    assert stack_backup.configured_volume_names(
        {
            "volumes": {
                "one": {"name": "project_one"},
                "external": {"name": "shared"},
                "invalid": None,
            }
        }
    ) == {"one": "project_one", "external": "shared"}


def test_manifest_checksum_validation(tmp_path: Path) -> None:
    payload = tmp_path / "payload.txt"
    payload.write_text("data", encoding="utf-8")
    manifest = {
        "schema_version": stack_backup.SCHEMA_VERSION,
        "checksums": {"payload.txt": stack_backup.sha256_file(payload)},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert stack_backup.read_and_verify_manifest(tmp_path) == manifest

    payload.write_text("changed", encoding="utf-8")
    with pytest.raises(stack_backup.BackupError, match="Checksum mismatch"):
        stack_backup.read_and_verify_manifest(tmp_path)


def test_safe_extract_rejects_parent_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo("../outside.txt")
        body = b"unsafe"
        info.size = len(body)
        output.addfile(info, io.BytesIO(body))
    with pytest.raises(stack_backup.BackupError, match="Unsafe archive member"):
        stack_backup.safe_extract(archive, tmp_path / "output")


def test_safe_extract_rejects_links(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        output.addfile(info)
    with pytest.raises(stack_backup.BackupError, match="Links are not allowed"):
        stack_backup.safe_extract(archive, tmp_path / "output")


def test_restore_parser_defaults_to_dry_run(tmp_path: Path) -> None:
    args = stack_backup.build_parser().parse_args(
        ["--root", str(tmp_path), "restore", "--archive", "backup.tar.gz"]
    )
    assert args.execute is False
    assert args.restore_documents is False
    assert args.restore_secrets is False
