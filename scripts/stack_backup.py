#!/usr/bin/env python3
"""Cold backup and guarded restore for the mrag + Dify Compose stack."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

SCHEMA_VERSION = 1
DURABLE_VOLUMES = (
    "mrag_backups",
    "mrag_converted_docs",
    "mrag_data",
    "mrag_qdrant_data",
)
CACHE_VOLUMES = (
    "docling_model_cache",
    "mrag_model_cache",
    "ollama_models",
)
HELPER_IMAGE_DEFAULT = "local/mrag-runtime:1.0.0"


class BackupError(RuntimeError):
    """An expected backup or restore validation failure."""


def run(
    command: Sequence[str],
    *,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def load_versions(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (root / "VERSIONS").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key] = value
    return result


def compose_command(root: Path) -> list[str]:
    if os.name == "nt":
        return [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "win-scripts" / "compose.ps1"),
        ]
    return [str(root / "scripts" / "compose.sh")]


def compose_config(root: Path) -> dict[str, Any]:
    completed = run(
        [*compose_command(root), "config", "--format", "json"], capture=True
    )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise BackupError(f"Docker Compose returned invalid JSON: {error}") from error


def project_name(config: dict[str, Any]) -> str:
    value = config.get("name")
    if not isinstance(value, str) or not value:
        raise BackupError("Compose configuration does not contain a project name")
    return value


def configured_volume_names(config: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    volumes = config.get("volumes", {})
    if not isinstance(volumes, dict):
        raise BackupError("Compose configuration has an invalid volumes section")
    for key, settings in volumes.items():
        if not isinstance(settings, dict):
            continue
        name = settings.get("name")
        if isinstance(name, str) and name:
            result[str(key)] = name
    return result


def running_container_ids(root: Path) -> list[str]:
    completed = run([*compose_command(root), "ps", "-q"], capture=True)
    return [line for line in completed.stdout.splitlines() if line.strip()]


def stop_if_requested(root: Path, stop_services: bool) -> bool:
    running = running_container_ids(root)
    if not running:
        return False
    if not stop_services:
        raise BackupError(
            "Compose services are running. Re-run with --stop-services for a "
            "consistent cold operation."
        )
    run([*compose_command(root), "stop"])
    if running_container_ids(root):
        raise BackupError("Some Compose services are still running after stop")
    return True


def restart_if_needed(root: Path, stopped: bool) -> None:
    if stopped:
        # A cold operation only stops existing containers. `start` resumes the
        # exact same containers without pulling images or applying new config.
        run([*compose_command(root), "start"])


def docker_volume_exists(name: str) -> bool:
    return (
        run(["docker", "volume", "inspect", name], capture=True, check=False).returncode
        == 0
    )


def archive_mount(source: str, archive: Path, helper_image: str) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{source}:/source:ro",
            "-v",
            f"{archive.parent.resolve()}:/backup",
            helper_image,
            "tar",
            "-C",
            "/source",
            "-czf",
            f"/backup/{archive.name}",
            ".",
        ]
    )


def restore_mount(archive: Path, target: str, helper_image: str) -> None:
    run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{target}:/target",
            "-v",
            f"{archive.parent.resolve()}:/backup:ro",
            helper_image,
            "sh",
            "-eu",
            "-c",
            (
                "find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; "
                f"tar -C /target -xzf /backup/{archive.name}"
            ),
        ]
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_checksums(staging: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for path in sorted(staging.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            checksums[path.relative_to(staging).as_posix()] = sha256_file(path)
    return checksums


def write_outer_archive(staging: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.partial")
    temporary.unlink(missing_ok=True)
    with tarfile.open(temporary, "w:gz") as output:
        for path in sorted(staging.iterdir()):
            output.add(path, arcname=path.name, recursive=True)
    temporary.chmod(0o600)
    temporary.replace(destination)


def create_backup(
    root: Path,
    output_dir: Path,
    *,
    stop_services: bool,
    include_documents: bool,
    include_caches: bool,
    label: str = "backup",
) -> Path:
    root = root.resolve()
    if not (root / ".env").is_file():
        raise BackupError("Missing .env; there is no configured stack to back up")
    config = compose_config(root)
    name = project_name(config)
    volumes = configured_volume_names(config)
    versions = load_versions(root)
    helper_image = versions.get("BACKUP_HELPER_IMAGE", HELPER_IMAGE_DEFAULT)
    stopped = stop_if_requested(root, stop_services)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = output_dir.resolve() / f"{name}-{label}-{timestamp}.tar.gz"

    try:
        with tempfile.TemporaryDirectory(prefix="mrag-dify-backup-") as temporary:
            staging = Path(temporary)
            secrets_dir = staging / "secrets"
            secrets_dir.mkdir()
            shutil.copy2(root / ".env", secrets_dir / ".env")
            (secrets_dir / ".env").chmod(0o600)

            host_archives: dict[str, str] = {}
            host_sources = {
                "mrag_profiles": root / "mrag_profiles",
                "dify_volumes": root / "vendor" / "dify" / "docker" / "volumes",
            }
            if include_documents:
                host_sources["kbs_docs"] = root / "kbs_docs"
            for key, source in host_sources.items():
                if source.exists():
                    archive = staging / "host" / f"{key}.tar.gz"
                    archive_mount(str(source.resolve()), archive, helper_image)
                    host_archives[key] = archive.relative_to(staging).as_posix()

            selected_keys = [*DURABLE_VOLUMES]
            if include_caches:
                selected_keys.extend(CACHE_VOLUMES)
            volume_archives: dict[str, dict[str, str]] = {}
            missing_volumes: list[str] = []
            for key in selected_keys:
                actual_name = volumes.get(key)
                if not actual_name or not docker_volume_exists(actual_name):
                    missing_volumes.append(key)
                    continue
                archive = staging / "volumes" / f"{key}.tar.gz"
                archive_mount(actual_name, archive, helper_image)
                volume_archives[key] = {
                    "name": actual_name,
                    "archive": archive.relative_to(staging).as_posix(),
                }

            manifest: dict[str, Any] = {
                "schema_version": SCHEMA_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "project_name": name,
                "project_version": versions.get("PROJECT_VERSION", "unknown"),
                "versions": versions,
                "include_documents": include_documents,
                "include_caches": include_caches,
                "host_archives": host_archives,
                "volume_archives": volume_archives,
                "missing_volumes": missing_volumes,
            }
            manifest["checksums"] = payload_checksums(staging)
            (staging / "manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            write_outer_archive(staging, destination)
    finally:
        restart_if_needed(root, stopped)

    print(f"Backup created: {destination}")
    if not include_documents:
        print("Source documents were not included (use --include-documents).")
    if not include_caches:
        print("Downloadable model caches were not included (use --include-caches).")
    return destination


def safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise BackupError(f"Unsafe archive member: {member.name}")
            if member.issym() or member.islnk():
                raise BackupError(f"Links are not allowed in backup archive: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise BackupError(f"Unsupported archive member: {member.name}")
        extract_options: dict[str, str] = {}
        if "filter" in inspect.signature(source.extractall).parameters:
            extract_options["filter"] = "data"
        source.extractall(destination, members=members, **extract_options)


def read_and_verify_manifest(staging: Path) -> dict[str, Any]:
    manifest_path = staging / "manifest.json"
    if not manifest_path.is_file():
        raise BackupError("Archive does not contain manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise BackupError(
            f"Unsupported backup schema: {manifest.get('schema_version')}"
        )
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict):
        raise BackupError("Manifest does not contain checksums")
    for relative, expected in checksums.items():
        path = staging / str(relative)
        if not path.is_file():
            raise BackupError(f"Backup payload is missing: {relative}")
        actual = sha256_file(path)
        if actual != expected:
            raise BackupError(f"Checksum mismatch: {relative}")
    return manifest


def restore_backup(
    root: Path,
    archive: Path,
    *,
    execute: bool,
    stop_services: bool,
    confirm_project: str | None,
    restore_documents: bool,
    restore_secrets: bool,
) -> None:
    root = root.resolve()
    archive = archive.resolve()
    if not archive.is_file():
        raise BackupError(f"Backup archive does not exist: {archive}")
    config = compose_config(root)
    current_project = project_name(config)
    current_volumes = configured_volume_names(config)
    versions = load_versions(root)
    helper_image = versions.get("BACKUP_HELPER_IMAGE", HELPER_IMAGE_DEFAULT)

    with tempfile.TemporaryDirectory(prefix="mrag-dify-restore-") as temporary:
        staging = Path(temporary)
        safe_extract(archive, staging)
        manifest = read_and_verify_manifest(staging)
        archived_project = manifest.get("project_name")
        print(f"Archive project: {archived_project}")
        print(f"Target project:  {current_project}")
        print(f"Archive version: {manifest.get('project_version')}")
        print("Checksums:       verified")
        print("Volumes:")
        for key in sorted(manifest.get("volume_archives", {})):
            print(f"  - {key} -> {current_volumes.get(key, 'not configured')}")
        print("Host data:")
        for key in sorted(manifest.get("host_archives", {})):
            action = "restore"
            if key == "kbs_docs" and not restore_documents:
                action = "skip (use --restore-documents)"
            print(f"  - {key}: {action}")
        print(
            "  - .env: "
            + ("restore" if restore_secrets else "skip (use --restore-secrets)")
        )

        if not execute:
            print("Dry run only. Add --execute and --confirm-project to restore.")
            return
        if confirm_project != current_project:
            raise BackupError(
                f"--confirm-project must exactly equal {current_project!r}"
            )
        if archived_project != current_project:
            raise BackupError(
                "Archive project name differs from the target. Restore into a "
                "matching isolated project first."
            )

        stopped = stop_if_requested(root, stop_services)
        try:
            safety = create_backup(
                root,
                archive.parent,
                stop_services=False,
                include_documents=restore_documents,
                include_caches=bool(manifest.get("include_caches")),
                label="pre-restore",
            )
            print(f"Pre-restore safety backup: {safety}")

            for key, details in manifest.get("volume_archives", {}).items():
                target = current_volumes.get(key)
                if not target:
                    raise BackupError(f"Target Compose volume is not configured: {key}")
                run(["docker", "volume", "create", target])
                restore_mount(staging / details["archive"], target, helper_image)

            host_targets = {
                "mrag_profiles": root / "mrag_profiles",
                "dify_volumes": root / "vendor" / "dify" / "docker" / "volumes",
            }
            if restore_documents:
                host_targets["kbs_docs"] = root / "kbs_docs"
            for key, target in host_targets.items():
                relative = manifest.get("host_archives", {}).get(key)
                if not relative:
                    continue
                target.mkdir(parents=True, exist_ok=True)
                restore_mount(staging / relative, str(target.resolve()), helper_image)

            if restore_secrets:
                restored_env = staging / "secrets" / ".env"
                if not restored_env.is_file():
                    raise BackupError("Archive does not contain secrets/.env")
                shutil.copy2(restored_env, root / ".env")
                if os.name != "nt":
                    (root / ".env").chmod(0o600)
        finally:
            restart_if_needed(root, stopped)

    print("Restore completed. Run ./scripts/verify.sh and a smoke search.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="create a cold stack backup")
    backup.add_argument("--output", type=Path, default=Path("backups"))
    backup.add_argument("--stop-services", action="store_true")
    backup.add_argument("--include-documents", action="store_true")
    backup.add_argument("--include-caches", action="store_true")

    restore = subparsers.add_parser("restore", help="validate or restore a backup")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--execute", action="store_true")
    restore.add_argument("--stop-services", action="store_true")
    restore.add_argument("--confirm-project")
    restore.add_argument("--restore-documents", action="store_true")
    restore.add_argument("--restore-secrets", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            output = args.output
            if not output.is_absolute():
                output = args.root / output
            create_backup(
                args.root,
                output,
                stop_services=args.stop_services,
                include_documents=args.include_documents,
                include_caches=args.include_caches,
            )
        else:
            restore_backup(
                args.root,
                args.archive,
                execute=args.execute,
                stop_services=args.stop_services,
                confirm_project=args.confirm_project,
                restore_documents=args.restore_documents,
                restore_secrets=args.restore_secrets,
            )
    except (BackupError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
