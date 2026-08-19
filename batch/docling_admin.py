#!/usr/bin/env python3
"""Offline administration commands for Docling conversion state."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from docling_ingestion import (
    DOCLING_CPU_IMAGE_DIGEST,
    DOCLING_NVIDIA_IMAGE_DIGEST,
    EXPECTED_DOCLING_SERVE_VERSION,
    EXPECTED_DOCLING_VERSION,
    ArtifactStore,
    ConversionManifest,
    ConversionPlan,
    DoclingSettings,
    ManifestError,
    build_conversion_plan,
    group_sources,
    load_conversion_manifest,
    partition_output_collisions,
    scan_sources,
)


def _manifest_path(converted_dir: Path) -> Path:
    return converted_dir / "conversion-manifest.json"


def _load_manifest(converted_dir: Path) -> ConversionManifest | None:
    return load_conversion_manifest(_manifest_path(converted_dir))


def _plan_payload(
    *,
    source_dir: Path,
    converted_dir: Path,
    fingerprint: str,
) -> dict[str, object]:
    store = ArtifactStore(converted_dir)
    scan = scan_sources(source_dir)
    groups, collision_issues = partition_output_collisions(
        group_sources(scan.documents)
    )
    source_issues = tuple(
        sorted(
            (*scan.issues, *collision_issues),
            key=lambda issue: issue.relative_path,
        )
    )
    manifest = _load_manifest(converted_dir)
    plan: ConversionPlan = build_conversion_plan(
        groups,
        manifest,
        conversion_fingerprint=fingerprint,
        output_exists=lambda relative: store.current_path(relative).is_file(),
    )
    return {
        "status": "ok",
        "mode": "dry-run",
        "summary": {
            **plan.summary(),
            "source_documents": len(scan.documents),
            "source_issues": len(source_issues),
        },
        "documents": [
            {
                "source_paths": list(item.source_group.source_paths),
                "source_sha256": item.source_group.sha256,
                "action": item.action.value,
                "reason": item.reason,
                "output_relative_path": item.output_relative_path,
                "has_previous_record": item.previous_record is not None,
            }
            for item in plan.documents
        ],
        "removed": [
            {
                "source_paths": list(record.source_paths),
                "source_sha256": record.source_sha256,
                "output_relative_path": record.output_relative_path,
            }
            for record in plan.removed_records
        ],
        "source_issues": [
            {
                "relative_path": issue.relative_path,
                "code": issue.code,
                "message": issue.message,
            }
            for issue in source_issues
        ],
    }


def _status_payload(converted_dir: Path) -> dict[str, object]:
    manifest = _load_manifest(converted_dir)
    if manifest is None:
        return {
            "status": "missing",
            "manifest": str(_manifest_path(converted_dir)),
        }
    unresolved = [
        {
            "source_paths": list(record.source_paths),
            "source_sha256": record.source_sha256,
            "status": record.status.value,
            "missing_pages": (
                list(record.missing_pages) if record.missing_pages is not None else None
            ),
            "missing_pages_known": record.missing_pages_known,
            "serving_previous_version": record.serving_previous_version,
            "served_source_sha256": record.served_source_sha256,
            "error_count": len(record.errors),
            "warning_count": len(record.warnings),
        }
        for record in manifest.documents
        if record.status.value != "success" or record.serving_previous_version
    ]
    return {
        "status": "ok",
        "schema_version": manifest.schema_version,
        "conversion_fingerprint": manifest.conversion_fingerprint,
        "completed_at": manifest.completed_at,
        "summary": manifest.summary(),
        "source_issues": [issue.to_dict() for issue in manifest.source_issues],
        "unresolved": unresolved,
    }


def _effective_settings(settings: object, *, image_digest: str) -> dict[str, object]:
    """Report the settings that decide conversion cost and output."""
    accelerator = settings.accelerator
    return {
        "accelerator": accelerator,
        "image": image_digest,
        "serve_url": settings.serve_url,
        "supported_extensions": list(settings.supported_extensions),
        "output_format": settings.output_format,
        "image_export_mode": settings.image_export_mode,
        "ocr_enabled": settings.ocr_enabled,
        "ocr_languages": list(settings.ocr_languages),
        "ocr_preset": settings.ocr_preset,
        "table_mode": settings.table_mode,
        "pipeline": settings.pipeline,
        "pdf_backend": settings.pdf_backend,
        "max_file_size": settings.max_file_size,
        "max_num_pages": settings.max_num_pages,
        "max_output_size": settings.max_output_size,
        "connection_timeout_seconds": settings.connection_timeout_seconds,
        "conversion_timeout_seconds": settings.conversion_timeout_seconds,
        "run_timeout_seconds": settings.run_timeout_seconds,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "max_retries": settings.max_retries,
        "batch_concurrency": settings.batch_concurrency,
    }


def _doctor_payload(*, probe: bool) -> dict[str, object]:
    """Report effective Docling settings and, optionally, live reachability.

    Settings are always reported so this stays useful without a running server;
    ``probe`` adds the network checks that need docling-serve to be up.

    httpx is imported lazily below rather than at module scope, because the
    other subcommands are documented to run offline and must not require it.
    """
    try:
        settings = DoclingSettings.from_environment()
    except ValueError as error:
        return {"status": "error", "stage": "settings", "message": str(error)}

    payload: dict[str, object] = {
        "status": "ok",
        "settings": _effective_settings(
            settings,
            image_digest=(
                DOCLING_NVIDIA_IMAGE_DIGEST
                if settings.accelerator == "nvidia"
                else DOCLING_CPU_IMAGE_DIGEST
            ),
        ),
        "expected": {
            "docling_serve": EXPECTED_DOCLING_SERVE_VERSION,
            "docling": EXPECTED_DOCLING_VERSION,
        },
    }
    if not probe:
        payload["probe"] = "skipped"
        return payload

    import httpx

    checks: dict[str, object] = {}
    try:
        with httpx.Client(timeout=settings.connection_timeout_seconds) as client:
            health = client.get(f"{settings.serve_url}/health")
            checks["health_status"] = health.status_code

            # /health and /version are unauthenticated on the pinned image, so
            # neither can prove the API key is right. Polling a task id that
            # cannot exist does: a wrong key gives 401, a good one gives 404,
            # and nothing is created either way.
            probe = client.get(
                f"{settings.serve_url}/v1/status/poll/"
                "00000000-0000-0000-0000-000000000000",
                headers={"X-Api-Key": settings.api_key},
            )
            checks["auth_probe_status"] = probe.status_code
            if probe.status_code in (401, 403):
                payload["status"] = "error"
                checks["authentication"] = "rejected"
                payload["checks"] = checks
                return payload
            checks["authentication"] = "accepted"

            version = client.get(f"{settings.serve_url}/version")
            checks["version_status"] = version.status_code
            version.raise_for_status()
            reported = version.json()
            checks["reported"] = reported
            mismatches = {
                name: (expected, reported.get(key))
                for name, key, expected in (
                    ("docling_serve", "docling-serve", EXPECTED_DOCLING_SERVE_VERSION),
                    ("docling", "docling", EXPECTED_DOCLING_VERSION),
                )
                if reported.get(key) != expected
            }
            if mismatches:
                payload["status"] = "error"
                checks["version_mismatch"] = {
                    name: {"expected": expected, "actual": actual}
                    for name, (expected, actual) in mismatches.items()
                }
    except Exception as error:  # noqa: BLE001 - a diagnostic reports every failure
        payload["status"] = "error"
        checks["error"] = f"{type(error).__name__}: {error}"

    payload["checks"] = checks
    return payload


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="build a conversion dry-run plan")
    plan.add_argument("--source-dir", type=Path, required=True)
    plan.add_argument("--converted-dir", type=Path, required=True)
    plan.add_argument("--fingerprint", required=True)

    status = commands.add_parser(
        "status",
        help="show manifest summary and unresolved documents",
    )
    status.add_argument("--converted-dir", type=Path, required=True)

    cleanup = commands.add_parser(
        "cleanup-staging",
        help="list or remove expired staging runs",
    )
    cleanup.add_argument("--converted-dir", type=Path, required=True)
    cleanup.add_argument("--older-than-seconds", type=int, required=True)
    cleanup.add_argument(
        "--execute",
        action="store_true",
        help="remove the listed runs; default is dry-run",
    )

    doctor = commands.add_parser(
        "doctor",
        help="report effective Docling settings and server reachability",
    )
    doctor.add_argument(
        "--no-probe",
        action="store_true",
        help="report settings only; skip every network check",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            payload = _plan_payload(
                source_dir=args.source_dir.resolve(),
                converted_dir=args.converted_dir.resolve(),
                fingerprint=args.fingerprint,
            )
            exit_code = 0
        elif args.command == "status":
            payload = _status_payload(args.converted_dir.resolve())
            exit_code = 0 if payload["status"] == "ok" else 1
        elif args.command == "doctor":
            payload = _doctor_payload(probe=not args.no_probe)
            exit_code = 0 if payload["status"] == "ok" else 1
        else:
            store = ArtifactStore(args.converted_dir.resolve())
            candidates = store.stale_staging_runs(
                older_than_seconds=args.older_than_seconds
            )
            removed = (
                store.cleanup_staging(older_than_seconds=args.older_than_seconds)
                if args.execute
                else ()
            )
            payload = {
                "status": "ok",
                "mode": "execute" if args.execute else "dry-run",
                "candidates": list(candidates),
                "removed": list(removed),
            }
            exit_code = 0
    except (ManifestError, OSError, RuntimeError, ValueError) as error:
        payload = {
            "status": "error",
            "error": type(error).__name__,
            "message": str(error),
        }
        exit_code = 1
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
