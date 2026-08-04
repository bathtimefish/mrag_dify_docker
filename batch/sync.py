#!/usr/bin/env python3
"""Convert source documents with Docling and synchronize Markdown into mrag.

The shared directory is the source of truth. Document identity follows mrag's
own behavior and is content-based (SHA-256), while the manifest preserves every
source relative path, including multiple paths with identical content.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

BATCH_DIR = Path(__file__).resolve().parent
if str(BATCH_DIR) not in sys.path:
    sys.path.insert(0, str(BATCH_DIR))

from docling_http_client import (
    DOCLING_CPU_IMAGE_DIGEST,
    DOCLING_NVIDIA_IMAGE_DIGEST,
    EXPECTED_DOCLING_SERVE_VERSION,
    EXPECTED_DOCLING_VERSION,
    HttpDoclingClient,
)
from docling_ingestion import (
    ArtifactStore,
    ConversionManifest,
    DoclingSettings,
    IngestionRunResult,
    IngestionRunStatus,
    build_conversion_plan,
    compute_conversion_fingerprint,
    find_orphan_artifacts,
    group_sources,
    load_conversion_manifest,
    run_conversion_pipeline,
    scan_sources,
    write_conversion_manifest,
)

# This scans Docling's converted output, not the originals, so it must match the
# set mrag itself accepts. Since 1.0.0 mrag rejects .pdf and every other format
# that needs external conversion, and a stray original reaching `mrag add` would
# fail the whole sync rather than be skipped here.
SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt"})
MANIFEST_SCHEMA_VERSION = 1
DOCLING_API_VERSION = "v1"


@dataclass(frozen=True)
class SourceDocument:
    relative_path: str
    absolute_path: Path
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class IndexedDocument:
    document_id: str
    filename: str
    sha256: str


@dataclass(frozen=True)
class SyncPlan:
    add_hashes: tuple[str, ...]
    remove_ids: tuple[str, ...]
    desired_hashes: tuple[str, ...]


CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def emit(event: str, **fields: object) -> None:
    print(
        json.dumps(
            {"timestamp": utc_now(), "event": event, **fields},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def verify_runtime() -> None:
    ollama_url = os.environ.get(
        "MRAG_OLLAMA_URL", "http://host.docker.internal:11434"
    ).rstrip("/")
    embedding_model = os.environ.get("MRAG_EMBEDDING_MODEL", "bge-m3:latest")
    with urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=10) as response:
        payload = json.load(response)
    model_names = {
        item.get("name") for item in payload.get("models", []) if item.get("name")
    }
    if embedding_model not in model_names:
        raise RuntimeError(
            f"required Ollama model {embedding_model!r} was not found at {ollama_url}"
        )

    from qdrant_client import QdrantClient

    qdrant_host = os.environ.get("MRAG_QDRANT_HOST", "mrag-qdrant")
    qdrant_port = int(os.environ.get("MRAG_QDRANT_PORT", "6333"))
    QdrantClient(host=qdrant_host, port=qdrant_port).get_collections()
    emit(
        "runtime_verified",
        ollama=ollama_url,
        embedding_model=embedding_model,
        qdrant=f"{qdrant_host}:{qdrant_port}",
    )


def sha256_file(path: Path) -> tuple[str, os.stat_result]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"file changed while hashing: {path}")
    return digest.hexdigest(), after


def scan_source(source_dir: Path) -> list[SourceDocument]:
    if not source_dir.is_dir():
        raise RuntimeError(f"source directory does not exist: {source_dir}")

    documents: list[SourceDocument] = []
    errors: list[str] = []
    for root, directories, files in os.walk(source_dir, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith(".") and not (Path(root) / name).is_symlink()
        )
        for filename in sorted(files):
            path = Path(root) / filename
            relative = path.relative_to(source_dir).as_posix()
            if filename.startswith(".") or path.is_symlink():
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            try:
                digest, stat = sha256_file(path)
                if stat.st_size == 0:
                    raise ValueError("zero-byte document")
                documents.append(
                    SourceDocument(
                        relative_path=relative,
                        absolute_path=path,
                        sha256=digest,
                        size=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                    )
                )
            except (OSError, RuntimeError, ValueError) as error:
                errors.append(f"{relative}: {error}")

    if errors:
        raise RuntimeError("source validation failed: " + "; ".join(errors))
    return documents


def load_indexed_documents(project_dir: Path) -> list[IndexedDocument]:
    database = project_dir / "mrag.db"
    if not database.is_file():
        raise RuntimeError(f"mrag database does not exist: {database}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT id, filename, file_hash FROM documents ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [
        IndexedDocument(document_id=row[0], filename=row[1], sha256=row[2])
        for row in rows
    ]


def load_index_health(project_dir: Path) -> dict[str, int]:
    """Count the per-document error and omission states mrag records.

    The post-index check only compares document hash sets, so a document can be
    present and still contribute nothing to retrieval. These counters surface
    that: ``fallback_no_vector`` chunks have no Qdrant point and are reachable
    by keyword search only, and excluded documents are omitted deliberately.
    """
    database = project_dir / "mrag.db"
    if not database.is_file():
        raise RuntimeError(f"mrag database does not exist: {database}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:

        def scalar(sql: str) -> int:
            try:
                row = connection.execute(sql).fetchone()
            except sqlite3.Error:
                # A table absent from an older schema is reported as zero rather
                # than failing the sync; verify checks the schema separately.
                return 0
            return int(row[0]) if row and row[0] is not None else 0

        return {
            "documents_error": scalar(
                "SELECT COUNT(*) FROM documents WHERE status = 'error'"
            ),
            "index_error": scalar(
                "SELECT COUNT(DISTINCT document_id) FROM document_indexes "
                "WHERE status = 'error'"
            ),
            "index_pending": scalar(
                "SELECT COUNT(DISTINCT document_id) FROM document_indexes "
                "WHERE status IN ('pending', 'indexing')"
            ),
            "documents_excluded": scalar(
                "SELECT COUNT(DISTINCT document_id) FROM document_exclusions "
                "WHERE revoked_at IS NULL"
            ),
            "documents_without_vectors": scalar(
                "SELECT COUNT(DISTINCT document_id) FROM chunk_variants "
                "WHERE metadata_json IS NOT NULL "
                "AND json_extract(metadata_json, '$.embedding_status') "
                "= 'fallback_no_vector'"
            ),
            "documents_raw_fallback": scalar(
                "SELECT COUNT(DISTINCT document_id) FROM chunk_variants "
                "WHERE metadata_json IS NOT NULL "
                "AND json_extract(metadata_json, '$.augmentation_status') "
                "= 'fallback_raw'"
            ),
        }
    finally:
        connection.close()


def group_sources_by_hash(
    documents: Iterable[SourceDocument],
) -> dict[str, list[SourceDocument]]:
    grouped: dict[str, list[SourceDocument]] = {}
    for document in documents:
        grouped.setdefault(document.sha256, []).append(document)
    for values in grouped.values():
        values.sort(key=lambda item: item.relative_path)
    return grouped


def build_plan(
    source_documents: Iterable[SourceDocument],
    indexed_documents: Iterable[IndexedDocument],
) -> SyncPlan:
    desired = group_sources_by_hash(source_documents)
    current_by_hash: dict[str, IndexedDocument] = {}
    duplicate_ids: list[str] = []
    for document in indexed_documents:
        if document.sha256 in current_by_hash:
            duplicate_ids.append(document.document_id)
        else:
            current_by_hash[document.sha256] = document

    desired_hashes = set(desired)
    current_hashes = set(current_by_hash)
    remove_ids = [
        current_by_hash[digest].document_id
        for digest in sorted(current_hashes - desired_hashes)
    ]
    remove_ids.extend(sorted(duplicate_ids))
    return SyncPlan(
        add_hashes=tuple(sorted(desired_hashes - current_hashes)),
        remove_ids=tuple(remove_ids),
        desired_hashes=tuple(sorted(desired_hashes)),
    )


def deletion_guard_reason(
    removal_count: int,
    current_count: int,
    max_percent: float,
    max_count: int,
) -> str | None:
    if removal_count == 0:
        return None
    percentage = (removal_count / current_count * 100) if current_count else 0
    if removal_count >= max_count:
        return f"removal count {removal_count} reached limit {max_count}"
    if percentage >= max_percent:
        return f"removal percentage {percentage:.1f}% reached limit {max_percent:.1f}%"
    return None


def default_runner(
    command: Sequence[str], working_directory: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=working_directory,
        check=True,
        text=True,
        capture_output=True,
    )


def run_command(
    runner: CommandRunner,
    command: Sequence[str],
    project_dir: Path,
) -> subprocess.CompletedProcess[str]:
    emit("command_started", command=list(command))
    try:
        result = runner(command, project_dir)
    except subprocess.CalledProcessError as error:
        emit(
            "command_failed",
            command=list(command),
            exit_code=error.returncode,
            stdout=error.stdout,
            stderr=error.stderr,
        )
        raise
    emit("command_completed", command=list(command), stdout=result.stdout.strip())
    return result


def add_document(
    runner: CommandRunner, project_dir: Path, source: SourceDocument
) -> str:
    result = run_command(
        runner, ["mrag", "add", str(source.absolute_path), "--json"], project_dir
    )
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        item = payload["items"][0]
        document_id = item["document_id"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"unexpected mrag add response: {result.stdout}") from error
    if not document_id:
        raise RuntimeError(f"mrag add returned no document ID: {result.stdout}")
    return str(document_id)


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise RuntimeError(f"unsupported manifest schema: {path}")
    return value


def desired_snapshot(documents: Iterable[SourceDocument]) -> list[dict[str, object]]:
    return [
        {
            "relative_path": item.relative_path,
            "sha256": item.sha256,
            "size": item.size,
            "mtime_ns": item.mtime_ns,
        }
        for item in sorted(documents, key=lambda value: value.relative_path)
    ]


def source_snapshot_from_manifest(
    manifest: dict,
) -> list[dict[str, object]] | None:
    records = manifest.get("documents")
    if not isinstance(records, list) or not all(
        isinstance(record, dict) for record in records
    ):
        return None
    source_fields = ("relative_path", "sha256", "size", "mtime_ns")
    return [
        {field: record.get(field) for field in source_fields}
        for record in records
    ]


def write_manifest(
    path: Path,
    source_documents: Iterable[SourceDocument],
    indexed_documents: Iterable[IndexedDocument],
) -> None:
    id_by_hash = {
        document.sha256: document.document_id for document in indexed_documents
    }
    records = desired_snapshot(source_documents)
    for record in records:
        record["document_id"] = id_by_hash[record["sha256"]]
    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "completed_at": utc_now(),
        "documents": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def create_backup(project_dir: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"mrag-project-{timestamp}.tar.gz"
    with tarfile.open(destination, "w:gz") as archive:
        for path in sorted(project_dir.rglob("*")):
            if path.name in {".sync.lock", "mrag.db-shm", "mrag.db-wal"}:
                continue
            archive.add(path, arcname=path.relative_to(project_dir))
    emit("backup_created", path=str(destination))
    return destination


def prune_backups(backup_dir: Path, retention: int) -> None:
    backups = sorted(
        backup_dir.glob("mrag-project-*.tar.gz"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for path in backups[max(retention, 0) :]:
        path.unlink()
        emit("backup_pruned", path=str(path))


def run_sync(
    *,
    source_dir: Path,
    project_dir: Path,
    backup_dir: Path,
    manifest_path: Path,
    max_delete_percent: float,
    max_delete_count: int,
    backup_retention: int,
    dry_run: bool,
    allow_large_delete: bool,
    completion_has_errors: bool = False,
    conversion_summary: dict[str, int] | None = None,
    runner: CommandRunner = default_runner,
) -> SyncPlan:
    source_documents = scan_source(source_dir)
    indexed_before = load_indexed_documents(project_dir)
    plan = build_plan(source_documents, indexed_before)
    old_manifest = load_manifest(manifest_path)
    snapshot_changed = (
        source_snapshot_from_manifest(old_manifest)
        != desired_snapshot(source_documents)
    )
    index_required = bool(plan.add_hashes or plan.remove_ids or snapshot_changed)

    emit(
        "sync_plan",
        source_paths=len(source_documents),
        unique_source_documents=len(plan.desired_hashes),
        indexed_documents=len(indexed_before),
        add_count=len(plan.add_hashes),
        remove_count=len(plan.remove_ids),
        index_required=index_required,
        dry_run=dry_run,
    )

    guard = deletion_guard_reason(
        len(plan.remove_ids),
        len(indexed_before),
        max_delete_percent,
        max_delete_count,
    )
    if guard and dry_run:
        emit("deletion_guard_warning", reason=guard)
        return plan
    if guard and not allow_large_delete:
        raise RuntimeError(
            f"large deletion guard stopped synchronization: {guard}; "
            "review with --dry-run and retry with --allow-large-delete"
        )
    if dry_run:
        return plan
    if not index_required:
        emit("sync_no_changes")
        return plan

    create_backup(project_dir, backup_dir)
    sources_by_hash = group_sources_by_hash(source_documents)

    for digest in plan.add_hashes:
        representative = sources_by_hash[digest][0]
        document_id = add_document(runner, project_dir, representative)
        emit(
            "document_added",
            relative_path=representative.relative_path,
            sha256=digest,
            document_id=document_id,
        )

    for document_id in plan.remove_ids:
        run_command(
            runner, ["mrag", "remove", document_id, "--force"], project_dir
        )
        emit("document_removed", document_id=document_id)

    run_command(runner, ["mrag", "index"], project_dir)

    indexed_after = load_indexed_documents(project_dir)
    actual_hashes = {item.sha256 for item in indexed_after}
    desired_hashes = set(plan.desired_hashes)
    if actual_hashes != desired_hashes:
        raise RuntimeError(
            "post-index validation failed: "
            f"missing={sorted(desired_hashes - actual_hashes)}, "
            f"unexpected={sorted(actual_hashes - desired_hashes)}"
        )

    write_manifest(manifest_path, source_documents, indexed_after)
    prune_backups(backup_dir, backup_retention)

    health = load_index_health(project_dir)
    degraded = {name: count for name, count in health.items() if count}
    if degraded:
        emit("index_health_degraded", **degraded)

    # One combined tally per run: how many documents Docling converted cleanly,
    # partially or not at all, and how many mrag then failed to index or omitted.
    conversion = {
        f"converted_{name}": count
        for name, count in (conversion_summary or {}).items()
    }
    emit(
        (
            "sync_completed_with_errors"
            if completion_has_errors or degraded
            else "sync_completed"
        ),
        source_paths=len(source_documents),
        indexed_documents=len(indexed_after),
        **conversion,
        **health,
    )
    return plan


def run_docling_phase(
    *,
    source_dir: Path,
    converted_root: Path,
    dry_run: bool,
    max_delete_percent: float,
    max_delete_count: int,
    allow_large_delete: bool,
    force: bool = False,
) -> IngestionRunResult | None:
    settings = DoclingSettings.from_environment()
    scan = scan_sources(
        source_dir,
        settings.supported_extensions,
        max_file_size=settings.max_file_size,
    )
    groups = group_sources(scan.documents)
    artifact_store = ArtifactStore(converted_root)
    manifest_path = converted_root / "conversion-manifest.json"
    previous_manifest = load_conversion_manifest(manifest_path)
    fingerprint = compute_conversion_fingerprint(
        docling_version=EXPECTED_DOCLING_VERSION,
        image_digest=(
            DOCLING_NVIDIA_IMAGE_DIGEST
            if settings.accelerator == "nvidia"
            else DOCLING_CPU_IMAGE_DIGEST
        ),
        api_version=DOCLING_API_VERSION,
        settings=settings,
    )

    if dry_run:
        artifact_store.prepare()
        current_paths = artifact_store.current_artifact_paths()
        plan = build_conversion_plan(
            groups,
            previous_manifest,
            conversion_fingerprint=fingerprint,
            output_exists=lambda relative: artifact_store.current_path(
                relative
            ).is_file(),
            orphan_paths=find_orphan_artifacts(
                current_paths,
                groups,
                previous_manifest,
            ),
            force=force,
        )
        emit(
            "conversion_plan",
            source_issues=len(scan.issues),
            dry_run=True,
            **plan.summary(),
        )
        guard = deletion_guard_reason(
            plan.removal_count(),
            max(
                len(previous_manifest.documents)
                if previous_manifest is not None
                else 0,
                len(current_paths),
            ),
            max_delete_percent,
            max_delete_count,
        )
        if guard:
            emit("conversion_deletion_guard_warning", reason=guard)
        for issue in scan.issues:
            emit(
                "source_validation_failed",
                relative_path=issue.relative_path,
                code=issue.code,
                message=issue.message,
            )
        return None

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    result = run_conversion_pipeline(
        source_groups=groups,
        source_issues=scan.issues,
        previous_manifest=previous_manifest,
        artifact_store=artifact_store,
        client=HttpDoclingClient(),
        settings=settings,
        conversion_fingerprint=fingerprint,
        converter_version=f"docling-serve {EXPECTED_DOCLING_SERVE_VERSION}",
        run_id=run_id,
        manifest_path=manifest_path,
        force=force,
        deletion_guard=(
            None
            if allow_large_delete
            else lambda removal_count, previous_count: (
                deletion_guard_reason(
                    removal_count,
                    previous_count,
                    max_delete_percent,
                    max_delete_count,
                )
            )
        ),
    )
    for event in result.events:
        print(
            json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":")),
            flush=True,
        )
    return result


def update_conversion_document_ids(
    converted_root: Path,
    indexed_documents: Sequence[IndexedDocument],
) -> None:
    manifest_path = converted_root / "conversion-manifest.json"
    manifest = load_conversion_manifest(manifest_path)
    if manifest is None:
        raise RuntimeError("conversion manifest disappeared after synchronization")
    id_by_hash = {
        document.sha256: document.document_id for document in indexed_documents
    }
    records = tuple(
        replace(
            record,
            mrag_document_id=(
                id_by_hash.get(record.output_sha256)
                if record.output_sha256 is not None
                else None
            ),
        )
        for record in manifest.documents
    )
    write_conversion_manifest(
        manifest_path,
        ConversionManifest(
            conversion_fingerprint=manifest.conversion_fingerprint,
            completed_at=manifest.completed_at,
            documents=records,
            source_issues=manifest.source_issues,
        ),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--source-dir",
        type=Path,
        default=Path(os.environ.get("MRAG_SOURCE_DIR", "/kbs_docs")),
    )
    result.add_argument(
        "--project-dir",
        type=Path,
        default=Path(os.environ.get("MRAG_PROJECT_DIR", "/data/mrag")),
    )
    result.add_argument(
        "--backup-dir",
        type=Path,
        default=Path(os.environ.get("MRAG_BACKUP_DIR", "/backups")),
    )
    result.add_argument(
        "--max-delete-percent",
        type=float,
        default=float(os.environ.get("MRAG_DELETE_MAX_PERCENT", "20")),
    )
    result.add_argument(
        "--max-delete-count",
        type=int,
        default=int(os.environ.get("MRAG_DELETE_MAX_COUNT", "50")),
    )
    result.add_argument(
        "--backup-retention",
        type=int,
        default=int(os.environ.get("MRAG_BACKUP_RETENTION", "7")),
    )
    result.add_argument(
        "--converted-root",
        type=Path,
        default=Path(
            os.environ.get("MRAG_CONVERTED_ROOT", "/data/converted-docs")
        ),
    )
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--allow-large-delete", action="store_true")
    result.add_argument(
        "--force-reconvert",
        action="store_true",
        help=(
            "re-convert every source even when unchanged; use after changing "
            "Docling settings that the conversion fingerprint does not cover"
        ),
    )
    result.add_argument(
        "--convert-only",
        action="store_true",
        help=(
            "run the conversion phase and stop before touching mrag; the "
            "converted tree and manifest are updated, the index is not"
        ),
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    project_dir = args.project_dir.resolve()
    manifest_path = project_dir / "sync-manifest.json"
    lock_path = project_dir / ".sync.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit("sync_skipped", reason="another synchronization is running")
            return 0
        try:
            conversion_result = run_docling_phase(
                source_dir=args.source_dir.resolve(),
                converted_root=args.converted_root.resolve(),
                dry_run=args.dry_run,
                max_delete_percent=args.max_delete_percent,
                max_delete_count=args.max_delete_count,
                allow_large_delete=args.allow_large_delete,
                force=args.force_reconvert,
            )
            if args.convert_only:
                # Still inside the exclusive lock, so a scheduled sync cannot
                # observe the converted tree between the two phases.
                emit(
                    "convert_only_completed",
                    dry_run=args.dry_run,
                    status=(
                        conversion_result.status.value
                        if conversion_result is not None
                        else "dry_run"
                    ),
                    **(
                        conversion_result.manifest.summary()
                        if conversion_result is not None
                        else {}
                    ),
                )
                return 0
            if not args.dry_run:
                verify_runtime()
            run_sync(
                source_dir=(args.converted_root / "current").resolve(),
                project_dir=project_dir,
                backup_dir=args.backup_dir.resolve(),
                manifest_path=manifest_path,
                max_delete_percent=args.max_delete_percent,
                max_delete_count=args.max_delete_count,
                backup_retention=args.backup_retention,
                dry_run=args.dry_run,
                allow_large_delete=args.allow_large_delete,
                completion_has_errors=(
                    conversion_result is not None
                    and conversion_result.status
                    is IngestionRunStatus.PARTIAL_SUCCESS
                ),
                conversion_summary=(
                    conversion_result.manifest.summary()
                    if conversion_result is not None
                    else None
                ),
            )
            if not args.dry_run:
                update_conversion_document_ids(
                    args.converted_root.resolve(),
                    load_indexed_documents(project_dir),
                )
        except Exception as error:  # noqa: BLE001 - CLI boundary reports all failures
            emit("sync_failed", error=type(error).__name__, message=str(error))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
