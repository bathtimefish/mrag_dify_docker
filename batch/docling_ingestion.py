"""Offline-safe building blocks for the future Docling ingestion pipeline.

This module deliberately contains no HTTP implementation and is not connected
to sync.py yet. The Docling API adapter will be added after the pinned image's
OpenAPI and real responses have been verified.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Protocol

# Pinned in VERSIONS and restated here so that offline tooling can report and
# check them without importing the HTTP adapter (which needs httpx).
EXPECTED_DOCLING_SERVE_VERSION = "1.28.0"
EXPECTED_DOCLING_VERSION = "2.115.0"
DOCLING_CPU_IMAGE_DIGEST = (
    "sha256:cc207e1eb768878456ed98042c5d84fae56af3729a9c03d3e5c8fef393902956"
)
DOCLING_NVIDIA_IMAGE_DIGEST = (
    "sha256:83de50eb65da256584d692c9e2e521df4b3c4b599024f6a844a190de9fc89dcd"
)

# Docling reports no error when a page range truncates a document, so the only
# way to detect it is to count page breaks in the output. This marker is asked
# for only when a page limit is configured, and is stripped before publishing.
PAGE_BREAK_SENTINEL = "<!--mrag-docling-page-break-->"

CONVERSION_MANIFEST_SCHEMA_VERSION = 1
CONVERSION_CLIENT_SCHEMA_VERSION = 1
SOURCE_HEADER_SCHEMA_VERSION = 1
MAX_DIAGNOSTIC_COMPONENT_CHARS = 128
MAX_DIAGNOSTIC_CODE_CHARS = 128
MAX_DIAGNOSTIC_MESSAGE_CHARS = 2000
MAX_DIAGNOSTIC_RAW_DETAIL_CHARS = 4000

DEFAULT_SUPPORTED_EXTENSIONS = (
    ".bmp",
    ".docx",
    ".jpeg",
    ".jpg",
    ".md",
    ".pdf",
    ".png",
    ".pptx",
    ".tif",
    ".tiff",
    ".txt",
    ".webp",
    ".xlsx",
)

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ConfigurationError(ValueError):
    """A Docling ingestion setting is invalid."""


class ManifestError(ValueError):
    """The conversion manifest is malformed or incompatible."""


class UnsafePathError(ValueError):
    """A path would escape a managed root."""


class DoclingServiceError(RuntimeError):
    """A systemic Docling failure that must abort the conversion run."""


class LargeDeletionError(RuntimeError):
    """The planned removal of converted artifacts exceeded the deletion guard."""


class ConversionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    SKIPPED = "skipped"
    FAILURE = "failure"


class ConversionAction(str, Enum):
    PUBLISH = "publish"
    RETAIN_PREVIOUS = "retain_previous"
    OMIT = "omit"


class ConversionPlanAction(str, Enum):
    CONVERT = "convert"
    REUSE = "reuse"


class IngestionRunStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"


def _parse_bool(name: str, raw: str) -> bool:
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        f"{name} must be one of true/false, 1/0, yes/no, or on/off; got {raw!r}"
    )


def _parse_positive_int(
    name: str,
    raw: str | None,
    *,
    default: int | None,
) -> int | None:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer; got {raw!r}") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero; got {value}")
    return value


def _normalize_extensions(raw: str) -> tuple[str, ...]:
    extensions: set[str] = set()
    for item in raw.split(","):
        value = item.strip().lower()
        if not value:
            continue
        if not value.startswith(".") or "/" in value or "\\" in value:
            raise ConfigurationError(
                "DOCLING_SUPPORTED_EXTENSIONS must contain comma-separated "
                f"file extensions; got {item!r}"
            )
        extensions.add(value)
    if not extensions:
        raise ConfigurationError("DOCLING_SUPPORTED_EXTENSIONS must not be empty")
    return tuple(sorted(extensions))


@dataclass(frozen=True)
class DoclingSettings:
    serve_url: str
    api_key: str
    accelerator: str = "cpu"
    supported_extensions: tuple[str, ...] = DEFAULT_SUPPORTED_EXTENSIONS
    output_format: str = "md"
    image_export_mode: str = "placeholder"
    ocr_enabled: bool = True
    ocr_languages: tuple[str, ...] = ("ja", "en")
    ocr_preset: str | None = None
    table_mode: str = "accurate"
    pipeline: str | None = None
    pdf_backend: str | None = None
    max_file_size: int | None = None
    max_num_pages: int | None = None
    max_output_size: int | None = None
    connection_timeout_seconds: int = 30
    conversion_timeout_seconds: int = 1800
    run_timeout_seconds: int | None = None
    poll_interval_seconds: int = 2
    max_retries: int = 3
    batch_concurrency: int = 1

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> DoclingSettings:
        values = os.environ if environment is None else environment
        serve_url = values.get("DOCLING_SERVE_URL", "http://docling-serve:5001").strip()
        if not serve_url.startswith(("http://", "https://")):
            raise ConfigurationError(
                "DOCLING_SERVE_URL must start with http:// or https://"
            )
        serve_url = serve_url.rstrip("/")

        api_key = values.get("DOCLING_SERVE_API_KEY", "").strip()
        if not api_key or api_key == "CHANGE_ME":
            raise ConfigurationError(
                "DOCLING_SERVE_API_KEY must be set to a non-placeholder value"
            )

        accelerator = values.get("DOCLING_ACCELERATOR", "cpu").strip().lower()
        if accelerator not in {"cpu", "nvidia"}:
            raise ConfigurationError("DOCLING_ACCELERATOR must be cpu or nvidia")

        output_format = values.get("DOCLING_OUTPUT_FORMAT", "md").strip().lower()
        if output_format != "md":
            raise ConfigurationError(
                "DOCLING_OUTPUT_FORMAT must be md for the mrag ingestion pipeline"
            )

        image_export_mode = (
            values.get("DOCLING_IMAGE_EXPORT_MODE", "placeholder").strip().lower()
        )
        if image_export_mode not in {"placeholder", "embedded", "referenced"}:
            raise ConfigurationError(
                "DOCLING_IMAGE_EXPORT_MODE must be placeholder, embedded, or referenced"
            )

        table_mode = values.get("DOCLING_TABLE_MODE", "accurate").strip().lower()
        if table_mode not in {"fast", "accurate"}:
            raise ConfigurationError("DOCLING_TABLE_MODE must be fast or accurate")

        ocr_languages = tuple(
            item.strip()
            for item in values.get("DOCLING_OCR_LANGUAGES", "ja,en").split(",")
            if item.strip()
        )
        if not ocr_languages:
            raise ConfigurationError("DOCLING_OCR_LANGUAGES must not be empty")

        return cls(
            serve_url=serve_url,
            api_key=api_key,
            accelerator=accelerator,
            supported_extensions=_normalize_extensions(
                values.get(
                    "DOCLING_SUPPORTED_EXTENSIONS",
                    ",".join(DEFAULT_SUPPORTED_EXTENSIONS),
                )
            ),
            output_format=output_format,
            image_export_mode=image_export_mode,
            ocr_enabled=_parse_bool(
                "DOCLING_OCR_ENABLED",
                values.get("DOCLING_OCR_ENABLED", "true"),
            ),
            ocr_languages=ocr_languages,
            ocr_preset=values.get("DOCLING_OCR_PRESET", "").strip() or None,
            table_mode=table_mode,
            pipeline=values.get("DOCLING_PIPELINE", "").strip() or None,
            pdf_backend=values.get("DOCLING_PDF_BACKEND", "").strip() or None,
            max_file_size=_parse_positive_int(
                "DOCLING_MAX_FILE_SIZE",
                values.get("DOCLING_MAX_FILE_SIZE"),
                default=None,
            ),
            max_num_pages=_parse_positive_int(
                "DOCLING_MAX_NUM_PAGES",
                values.get("DOCLING_MAX_NUM_PAGES"),
                default=None,
            ),
            max_output_size=_parse_positive_int(
                "DOCLING_MAX_OUTPUT_SIZE",
                values.get("DOCLING_MAX_OUTPUT_SIZE"),
                default=None,
            ),
            connection_timeout_seconds=_parse_positive_int(
                "DOCLING_CONNECTION_TIMEOUT_SECONDS",
                values.get("DOCLING_CONNECTION_TIMEOUT_SECONDS"),
                default=30,
            )
            or 30,
            conversion_timeout_seconds=_parse_positive_int(
                "DOCLING_CONVERSION_TIMEOUT_SECONDS",
                values.get("DOCLING_CONVERSION_TIMEOUT_SECONDS"),
                default=1800,
            )
            or 1800,
            # No default: an unset run budget keeps converting until every
            # planned document has been attempted, which is what a manual or
            # first-time run needs. Cron-driven runs set it to the interval.
            run_timeout_seconds=_parse_positive_int(
                "DOCLING_RUN_TIMEOUT_SECONDS",
                values.get("DOCLING_RUN_TIMEOUT_SECONDS"),
                default=None,
            ),
            poll_interval_seconds=_parse_positive_int(
                "DOCLING_POLL_INTERVAL_SECONDS",
                values.get("DOCLING_POLL_INTERVAL_SECONDS"),
                default=2,
            )
            or 2,
            max_retries=_parse_positive_int(
                "DOCLING_MAX_RETRIES",
                values.get("DOCLING_MAX_RETRIES"),
                default=3,
            )
            or 3,
            batch_concurrency=_parse_positive_int(
                "DOCLING_BATCH_CONCURRENCY",
                values.get("DOCLING_BATCH_CONCURRENCY"),
                default=1,
            )
            or 1,
        )

    def fingerprint_options(self) -> dict[str, object]:
        """Return non-secret settings that affect converted content."""
        return {
            "output_format": self.output_format,
            "image_export_mode": self.image_export_mode,
            "ocr_enabled": self.ocr_enabled,
            "ocr_languages": list(self.ocr_languages),
            "ocr_preset": self.ocr_preset,
            "table_mode": self.table_mode,
            "pipeline": self.pipeline,
            "pdf_backend": self.pdf_backend,
            # Changing the page limit changes which documents are publishable at
            # all, so it must invalidate previously converted output.
            "max_num_pages": self.max_num_pages,
        }


def compute_conversion_fingerprint(
    *,
    docling_version: str,
    image_digest: str,
    api_version: str,
    settings: DoclingSettings,
) -> str:
    """Hash every versioned setting that can affect generated Markdown."""
    identity = {
        "docling_version": docling_version.strip(),
        "image_digest": image_digest.strip(),
        "api_version": api_version.strip(),
    }
    for name, value in identity.items():
        if not value:
            raise ConfigurationError(f"{name} must not be empty")
    payload = {
        **identity,
        "conversion_options": settings.fingerprint_options(),
        "source_header_schema_version": SOURCE_HEADER_SCHEMA_VERSION,
        "conversion_client_schema_version": CONVERSION_CLIENT_SCHEMA_VERSION,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


@dataclass(frozen=True)
class SourceDocument:
    relative_path: str
    absolute_path: Path
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class SourceIssue:
    relative_path: str
    code: str
    message: str

    def __post_init__(self) -> None:
        _safe_relative_path(self.relative_path)
        if len(self.code) > MAX_DIAGNOSTIC_CODE_CHARS:
            raise ManifestError("source issue code exceeds maximum length")
        if len(self.message) > MAX_DIAGNOSTIC_MESSAGE_CHARS:
            raise ManifestError("source issue message exceeds maximum length")

    @classmethod
    def normalized(
        cls,
        *,
        relative_path: str,
        code: object,
        message: object,
    ) -> SourceIssue:
        return cls(
            relative_path=relative_path,
            code=sanitize_diagnostic(
                code,
                max_chars=MAX_DIAGNOSTIC_CODE_CHARS,
            ),
            message=sanitize_diagnostic(
                message,
                max_chars=MAX_DIAGNOSTIC_MESSAGE_CHARS,
            ),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "relative_path": self.relative_path,
            "code": self.code,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, value: object) -> SourceIssue:
        if not isinstance(value, dict):
            raise ManifestError("source issue entry must be an object")
        try:
            relative_path = str(value["relative_path"])
            _safe_relative_path(relative_path)
            return cls.normalized(
                relative_path=relative_path,
                code=value["code"],
                message=value["message"],
            )
        except KeyError as error:
            raise ManifestError(
                f"missing source issue field: {error.args[0]}"
            ) from error


@dataclass(frozen=True)
class SourceScan:
    documents: tuple[SourceDocument, ...]
    issues: tuple[SourceIssue, ...]


@dataclass(frozen=True)
class SourceGroup:
    sha256: str
    sources: tuple[SourceDocument, ...]

    @property
    def representative(self) -> SourceDocument:
        return self.sources[0]

    @property
    def source_paths(self) -> tuple[str, ...]:
        return tuple(source.relative_path for source in self.sources)


def _sha256_file_stable(path: Path) -> tuple[str, os.stat_result]:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError("file changed while hashing")
    return digest.hexdigest(), after


def scan_sources(
    source_dir: Path,
    supported_extensions: Sequence[str] = DEFAULT_SUPPORTED_EXTENSIONS,
    *,
    max_file_size: int | None = None,
) -> SourceScan:
    """Scan sources without allowing one malformed document to abort the scan."""
    if not source_dir.is_dir():
        raise RuntimeError(f"source directory does not exist: {source_dir}")
    allowed = {item.lower() for item in supported_extensions}
    documents: list[SourceDocument] = []
    issues: list[SourceIssue] = []

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
            if path.suffix.lower() not in allowed:
                continue
            try:
                digest, stat = _sha256_file_stable(path)
                if stat.st_size == 0:
                    raise ValueError("zero-byte document")
                if max_file_size is not None and stat.st_size > max_file_size:
                    raise ValueError(
                        f"document size {stat.st_size} exceeds limit {max_file_size}"
                    )
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
                issues.append(
                    SourceIssue.normalized(
                        relative_path=relative,
                        code="source_validation_failed",
                        message=error,
                    )
                )

    return SourceScan(
        documents=tuple(sorted(documents, key=lambda item: item.relative_path)),
        issues=tuple(sorted(issues, key=lambda item: item.relative_path)),
    )


def group_sources(documents: Sequence[SourceDocument]) -> tuple[SourceGroup, ...]:
    grouped: dict[str, list[SourceDocument]] = {}
    for document in documents:
        grouped.setdefault(document.sha256, []).append(document)
    return tuple(
        SourceGroup(
            sha256=digest,
            sources=tuple(sorted(values, key=lambda item: item.relative_path)),
        )
        for digest, values in sorted(grouped.items())
    )


def _safe_relative_path(value: str) -> PurePosixPath:
    relative = PurePosixPath(value)
    if (
        not value
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise UnsafePathError(f"unsafe relative path: {value!r}")
    return relative


def converted_relative_path(source_relative_path: str) -> str:
    source = _safe_relative_path(source_relative_path)
    return f"{source.as_posix()}.md"


def _resolve_managed_path(root: Path, relative_path: str) -> Path:
    relative = _safe_relative_path(relative_path)
    resolved_root = root.resolve()
    candidate = (resolved_root / Path(*relative.parts)).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as error:
        raise UnsafePathError(
            f"path escapes managed root: {relative_path!r}"
        ) from error
    return candidate


def render_source_header(
    *,
    source_path: str,
    source_sha256: str,
    status: ConversionStatus,
    converter_version: str,
) -> str:
    _safe_relative_path(source_path)
    if status not in {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}:
        raise ValueError("only publishable conversions can have a source header")
    lines = [
        f"> Source file: <code>{html.escape(source_path)}</code>",
        f"> Source SHA-256: <code>{html.escape(source_sha256)}</code>",
        f"> Conversion status: <code>{status.value}</code>",
    ]
    if status is ConversionStatus.PARTIAL_SUCCESS:
        lines.append(
            "> Conversion warning: Some source content could not be converted. "
            "See the ingestion manifest."
        )
    lines.append(f"> Converted by: <code>{html.escape(converter_version)}</code>")
    return "\n".join(lines) + "\n\n"


def count_pages(markdown: str) -> int:
    """Return the page count implied by the page-break markers Docling wrote.

    Formats without pagination produce no markers, which correctly reads as a
    single page.
    """
    return markdown.count(PAGE_BREAK_SENTINEL) + 1


def strip_page_break_sentinel(markdown: str) -> str:
    """Remove the counting markers so they never reach the knowledge base.

    Docling emits the marker as its own paragraph, so the surrounding blank
    lines are collapsed back to a single paragraph break.
    """
    without_paragraphs = re.sub(
        r"\n\s*" + re.escape(PAGE_BREAK_SENTINEL) + r"\s*\n",
        "\n\n",
        markdown,
    )
    # Any marker left in an unexpected position is still removed outright.
    collapsed = without_paragraphs.replace(PAGE_BREAK_SENTINEL, "")
    return re.sub(r"\n{3,}", "\n\n", collapsed)


def build_published_markdown(
    *,
    markdown: str,
    source_path: str,
    source_sha256: str,
    status: ConversionStatus,
    converter_version: str,
    max_output_size: int | None = None,
) -> str:
    if not markdown.strip():
        raise ValueError("Docling returned empty Markdown")
    body = markdown.lstrip("\ufeff")
    result = (
        render_source_header(
            source_path=source_path,
            source_sha256=source_sha256,
            status=status,
            converter_version=converter_version,
        )
        + body
    )
    output_size = len(result.encode("utf-8"))
    if max_output_size is not None and output_size > max_output_size:
        raise ValueError(
            f"converted Markdown size {output_size} exceeds limit {max_output_size}"
        )
    return result


def sanitize_diagnostic(
    value: object,
    *,
    max_chars: int,
    secrets: Sequence[str] = (),
) -> str:
    """Remove control characters, redact known secrets, and bound diagnostics."""
    if max_chars <= 0:
        raise ValueError("max_chars must be greater than zero")
    result = str(value)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    result = "".join(
        character
        for character in result
        if character in {"\n", "\t"} or ord(character) >= 32
    )
    if len(result) <= max_chars:
        return result
    suffix = "...[truncated]"
    if max_chars <= len(suffix):
        return suffix[:max_chars]
    return result[: max_chars - len(suffix)] + suffix


@dataclass(frozen=True)
class ConversionError:
    component: str
    code: str
    message: str
    page: int | None = None
    raw_detail: str | None = None

    def __post_init__(self) -> None:
        limits = {
            "component": (self.component, MAX_DIAGNOSTIC_COMPONENT_CHARS),
            "code": (self.code, MAX_DIAGNOSTIC_CODE_CHARS),
            "message": (self.message, MAX_DIAGNOSTIC_MESSAGE_CHARS),
            "raw_detail": (
                self.raw_detail,
                MAX_DIAGNOSTIC_RAW_DETAIL_CHARS,
            ),
        }
        for name, (value, limit) in limits.items():
            if value is not None and len(value) > limit:
                raise ManifestError(
                    f"conversion error {name} exceeds maximum length {limit}"
                )

    @classmethod
    def normalized(
        cls,
        *,
        component: object,
        code: object,
        message: object,
        page: int | None = None,
        raw_detail: object | None = None,
        secrets: Sequence[str] = (),
    ) -> ConversionError:
        return cls(
            component=sanitize_diagnostic(
                component,
                max_chars=MAX_DIAGNOSTIC_COMPONENT_CHARS,
                secrets=secrets,
            ),
            code=sanitize_diagnostic(
                code,
                max_chars=MAX_DIAGNOSTIC_CODE_CHARS,
                secrets=secrets,
            ),
            message=sanitize_diagnostic(
                message,
                max_chars=MAX_DIAGNOSTIC_MESSAGE_CHARS,
                secrets=secrets,
            ),
            page=page,
            raw_detail=(
                sanitize_diagnostic(
                    raw_detail,
                    max_chars=MAX_DIAGNOSTIC_RAW_DETAIL_CHARS,
                    secrets=secrets,
                )
                if raw_detail is not None
                else None
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "code": self.code,
            "message": self.message,
            "page": self.page,
            "raw_detail": self.raw_detail,
        }

    @classmethod
    def from_dict(cls, value: object) -> ConversionError:
        if not isinstance(value, dict):
            raise ManifestError("conversion error entry must be an object")
        page = value.get("page")
        if page is not None and (not isinstance(page, int) or page <= 0):
            raise ManifestError("conversion error page must be a positive integer")
        return cls(
            component=str(value.get("component", "")),
            code=str(value.get("code", "")),
            message=str(value.get("message", "")),
            page=page,
            raw_detail=(
                str(value["raw_detail"])
                if value.get("raw_detail") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ConversionAttempt:
    status: ConversionStatus
    markdown: str | None = None
    task_id: str | None = None
    warnings: tuple[str, ...] = ()
    errors: tuple[ConversionError, ...] = ()
    missing_pages: tuple[int, ...] | None = None
    missing_pages_known: bool = False
    processing_time_seconds: float | None = None
    retry_count: int = 0


class DoclingClient(Protocol):
    """Contract to implement after the real Docling API has been verified."""

    def convert(
        self,
        source: SourceDocument,
        settings: DoclingSettings,
    ) -> ConversionAttempt:
        """Convert one source and return a normalized result."""


@dataclass(frozen=True)
class ConversionDecision:
    action: ConversionAction
    effective_status: ConversionStatus
    reason: str


def decide_conversion(
    attempt: ConversionAttempt,
    *,
    previous_record_exists: bool,
    previous_output_exists: bool,
) -> ConversionDecision:
    publishable = attempt.status in {
        ConversionStatus.SUCCESS,
        ConversionStatus.PARTIAL_SUCCESS,
    }
    if publishable and attempt.markdown and attempt.markdown.strip():
        return ConversionDecision(
            action=ConversionAction.PUBLISH,
            effective_status=attempt.status,
            reason="conversion output is publishable",
        )

    reason = (
        "Docling returned empty Markdown"
        if publishable
        else f"Docling returned {attempt.status.value}"
    )
    if previous_record_exists and previous_output_exists:
        return ConversionDecision(
            action=ConversionAction.RETAIN_PREVIOUS,
            effective_status=ConversionStatus.FAILURE,
            reason=reason,
        )
    return ConversionDecision(
        action=ConversionAction.OMIT,
        effective_status=ConversionStatus.FAILURE,
        reason=reason,
    )


@dataclass(frozen=True)
class ConversionRecord:
    source_sha256: str
    source_paths: tuple[str, ...]
    representative_path: str
    source_size: int
    source_mtime_ns: int
    conversion_fingerprint: str
    status: ConversionStatus
    attempted_at: str
    converted_at: str | None = None
    task_id: str | None = None
    retry_count: int = 0
    output_relative_path: str | None = None
    output_sha256: str | None = None
    missing_pages: tuple[int, ...] | None = None
    missing_pages_known: bool = False
    errors: tuple[ConversionError, ...] = ()
    warnings: tuple[str, ...] = ()
    processing_time_seconds: float | None = None
    serving_previous_version: bool = False
    served_source_sha256: str | None = None
    mrag_document_id: str | None = None

    def __post_init__(self) -> None:
        if not self.source_paths:
            raise ManifestError("source_paths must not be empty")
        for source_path in self.source_paths:
            _safe_relative_path(source_path)
        if self.representative_path not in self.source_paths:
            raise ManifestError("representative_path must be present in source_paths")
        if self.output_relative_path is not None:
            _safe_relative_path(self.output_relative_path)
        if self.retry_count < 0:
            raise ManifestError("retry_count must not be negative")
        if self.missing_pages_known and self.missing_pages is None:
            raise ManifestError(
                "missing_pages must be a list when missing_pages_known is true"
            )
        if self.missing_pages is not None and any(
            page <= 0 for page in self.missing_pages
        ):
            raise ManifestError("missing_pages must contain positive page numbers")
        if self.serving_previous_version and not self.served_source_sha256:
            raise ManifestError(
                "served_source_sha256 is required when serving a previous version"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_sha256": self.source_sha256,
            "source_paths": list(self.source_paths),
            "representative_path": self.representative_path,
            "source_size": self.source_size,
            "source_mtime_ns": self.source_mtime_ns,
            "conversion_fingerprint": self.conversion_fingerprint,
            "status": self.status.value,
            "attempted_at": self.attempted_at,
            "converted_at": self.converted_at,
            "task_id": self.task_id,
            "retry_count": self.retry_count,
            "output_relative_path": self.output_relative_path,
            "output_sha256": self.output_sha256,
            "missing_pages": (
                list(self.missing_pages) if self.missing_pages is not None else None
            ),
            "missing_pages_known": self.missing_pages_known,
            "errors": [item.to_dict() for item in self.errors],
            "warnings": list(self.warnings),
            "processing_time_seconds": self.processing_time_seconds,
            "serving_previous_version": self.serving_previous_version,
            "served_source_sha256": self.served_source_sha256,
            "mrag_document_id": self.mrag_document_id,
        }

    @classmethod
    def from_dict(cls, value: object) -> ConversionRecord:
        if not isinstance(value, dict):
            raise ManifestError("conversion document entry must be an object")
        try:
            status = ConversionStatus(value["status"])
            source_paths_value = value["source_paths"]
            if not isinstance(source_paths_value, list) or not all(
                isinstance(item, str) for item in source_paths_value
            ):
                raise ManifestError("source_paths must be a list of strings")
            missing_pages_value = value.get("missing_pages")
            if missing_pages_value is not None and (
                not isinstance(missing_pages_value, list)
                or not all(isinstance(item, int) for item in missing_pages_value)
            ):
                raise ManifestError("missing_pages must be a list of integers or null")
            errors_value = value.get("errors", [])
            if not isinstance(errors_value, list):
                raise ManifestError("errors must be a list")
            warnings_value = value.get("warnings", [])
            if not isinstance(warnings_value, list) or not all(
                isinstance(item, str) for item in warnings_value
            ):
                raise ManifestError("warnings must be a list of strings")
            return cls(
                source_sha256=str(value["source_sha256"]),
                source_paths=tuple(source_paths_value),
                representative_path=str(value["representative_path"]),
                source_size=int(value["source_size"]),
                source_mtime_ns=int(value["source_mtime_ns"]),
                conversion_fingerprint=str(value["conversion_fingerprint"]),
                status=status,
                attempted_at=str(value["attempted_at"]),
                converted_at=(
                    str(value["converted_at"])
                    if value.get("converted_at") is not None
                    else None
                ),
                task_id=(
                    str(value["task_id"]) if value.get("task_id") is not None else None
                ),
                retry_count=int(value.get("retry_count", 0)),
                output_relative_path=(
                    str(value["output_relative_path"])
                    if value.get("output_relative_path") is not None
                    else None
                ),
                output_sha256=(
                    str(value["output_sha256"])
                    if value.get("output_sha256") is not None
                    else None
                ),
                missing_pages=(
                    tuple(missing_pages_value)
                    if missing_pages_value is not None
                    else None
                ),
                missing_pages_known=bool(value.get("missing_pages_known", False)),
                errors=tuple(ConversionError.from_dict(item) for item in errors_value),
                warnings=tuple(warnings_value),
                processing_time_seconds=(
                    float(value["processing_time_seconds"])
                    if value.get("processing_time_seconds") is not None
                    else None
                ),
                serving_previous_version=bool(
                    value.get("serving_previous_version", False)
                ),
                served_source_sha256=(
                    str(value["served_source_sha256"])
                    if value.get("served_source_sha256") is not None
                    else None
                ),
                mrag_document_id=(
                    str(value["mrag_document_id"])
                    if value.get("mrag_document_id") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, ManifestError):
                raise
            raise ManifestError(
                f"invalid conversion document entry: {error}"
            ) from error


@dataclass(frozen=True)
class ConversionManifest:
    conversion_fingerprint: str
    completed_at: str
    documents: tuple[ConversionRecord, ...]
    source_issues: tuple[SourceIssue, ...] = ()
    schema_version: int = CONVERSION_MANIFEST_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONVERSION_MANIFEST_SCHEMA_VERSION:
            raise ManifestError(
                f"unsupported conversion manifest schema: {self.schema_version}"
            )
        hashes = [document.source_sha256 for document in self.documents]
        if len(hashes) != len(set(hashes)):
            raise ManifestError("conversion manifest contains duplicate source hashes")

    def summary(self) -> dict[str, int]:
        result = {
            "success": 0,
            "partial_success": 0,
            "failure": 0,
            "serving_previous_version": 0,
        }
        for document in self.documents:
            if document.status is ConversionStatus.SUCCESS:
                result["success"] += 1
            elif document.status is ConversionStatus.PARTIAL_SUCCESS:
                result["partial_success"] += 1
            else:
                result["failure"] += 1
            if document.serving_previous_version:
                result["serving_previous_version"] += 1
        return result

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "conversion_fingerprint": self.conversion_fingerprint,
            "completed_at": self.completed_at,
            "summary": self.summary(),
            "documents": [document.to_dict() for document in self.documents],
            "source_issues": [issue.to_dict() for issue in self.source_issues],
        }

    @classmethod
    def from_dict(cls, value: object) -> ConversionManifest:
        if not isinstance(value, dict):
            raise ManifestError("conversion manifest must be an object")
        if value.get("schema_version") != CONVERSION_MANIFEST_SCHEMA_VERSION:
            raise ManifestError(
                f"unsupported conversion manifest schema: "
                f"{value.get('schema_version')!r}"
            )
        documents = value.get("documents")
        if not isinstance(documents, list):
            raise ManifestError("conversion manifest documents must be a list")
        source_issues = value.get("source_issues", [])
        if not isinstance(source_issues, list):
            raise ManifestError("conversion manifest source_issues must be a list")
        try:
            return cls(
                conversion_fingerprint=str(value["conversion_fingerprint"]),
                completed_at=str(value["completed_at"]),
                documents=tuple(
                    ConversionRecord.from_dict(document) for document in documents
                ),
                source_issues=tuple(
                    SourceIssue.from_dict(issue) for issue in source_issues
                ),
            )
        except KeyError as error:
            raise ManifestError(
                f"missing conversion manifest field: {error.args[0]}"
            ) from error


def load_conversion_manifest(path: Path) -> ConversionManifest | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(
            f"could not read conversion manifest {path}: {error}"
        ) from error
    return ConversionManifest.from_dict(value)


def write_conversion_manifest(path: Path, manifest: ConversionManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                manifest.to_dict(),
                stream,
                ensure_ascii=False,
                indent=2,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class PlannedConversion:
    source_group: SourceGroup
    action: ConversionPlanAction
    reason: str
    output_relative_path: str
    previous_record: ConversionRecord | None = None


@dataclass(frozen=True)
class ConversionPlan:
    documents: tuple[PlannedConversion, ...]
    removed_records: tuple[ConversionRecord, ...]
    orphan_paths: tuple[str, ...] = ()

    def summary(self) -> dict[str, int]:
        return {
            "convert": sum(
                item.action is ConversionPlanAction.CONVERT for item in self.documents
            ),
            "reuse": sum(
                item.action is ConversionPlanAction.REUSE for item in self.documents
            ),
            "remove": len(self.removed_records),
            "orphan": len(self.orphan_paths),
        }

    def removal_count(self) -> int:
        """Count every artifact the run would delete, from both causes."""
        return len(self.removed_records) + len(self.orphan_paths)


def find_orphan_artifacts(
    current_paths: Sequence[str],
    source_groups: Sequence[SourceGroup],
    previous_manifest: ConversionManifest | None,
) -> tuple[str, ...]:
    """Find published artifacts that neither a source nor the manifest explains.

    Artifacts the manifest still tracks are deleted by the ordinary plan, so what
    is left here are files no bookkeeping accounts for: output published by a run
    that died before writing the manifest, or output whose source was deleted
    while the manifest was missing. Anything a present source could still produce
    is kept, so a failed conversion never costs a document its existing Markdown.
    """
    keep = {
        converted_relative_path(group.representative.relative_path)
        for group in source_groups
    }
    if previous_manifest is not None:
        keep.update(
            record.output_relative_path
            for record in previous_manifest.documents
            if record.output_relative_path is not None
        )
    return tuple(path for path in current_paths if path not in keep)


def build_conversion_plan(
    source_groups: Sequence[SourceGroup],
    previous_manifest: ConversionManifest | None,
    *,
    conversion_fingerprint: str,
    output_exists: Callable[[str], bool],
    orphan_paths: Sequence[str] = (),
    force: bool = False,
) -> ConversionPlan:
    """Plan conversion without touching Docling, artifacts, or mrag state.

    ``force`` re-converts every current source even when nothing changed. It
    never widens the removal set: documents are still removed only because their
    source disappeared, so a forced run cannot delete more than a normal one.
    """
    previous_records = (
        list(previous_manifest.documents) if previous_manifest is not None else []
    )
    previous_by_hash = {
        record.source_sha256: index for index, record in enumerate(previous_records)
    }
    assigned: dict[str, int] = {}
    used_previous: set[int] = set()

    # Preserve the strongest identity first. This matters when one former
    # duplicate group splits into multiple different source hashes.
    for group in source_groups:
        previous_index = previous_by_hash.get(group.sha256)
        if previous_index is not None:
            assigned[group.sha256] = previous_index
            used_previous.add(previous_index)

    # A changed source has a new hash. Associate it with one unclaimed previous
    # record by source-path overlap so a failed update can retain the old output.
    for group in source_groups:
        if group.sha256 in assigned:
            continue
        paths = set(group.source_paths)
        candidates: list[tuple[int, str, int]] = []
        for index, record in enumerate(previous_records):
            if index in used_previous:
                continue
            overlap = len(paths.intersection(record.source_paths))
            if overlap:
                candidates.append((-overlap, record.representative_path, index))
        if candidates:
            _, _, previous_index = min(candidates)
            assigned[group.sha256] = previous_index
            used_previous.add(previous_index)

    planned: list[PlannedConversion] = []
    for group in source_groups:
        expected_output = converted_relative_path(group.representative.relative_path)
        previous_index = assigned.get(group.sha256)
        previous = (
            previous_records[previous_index] if previous_index is not None else None
        )
        if force:
            action = ConversionPlanAction.CONVERT
            reason = "forced re-conversion"
        elif previous is None:
            action = ConversionPlanAction.CONVERT
            reason = "new source"
        elif previous.source_sha256 != group.sha256:
            action = ConversionPlanAction.CONVERT
            reason = "source content changed"
        elif previous.serving_previous_version:
            action = ConversionPlanAction.CONVERT
            reason = "retry failed update while serving previous version"
        elif previous.status not in {
            ConversionStatus.SUCCESS,
            ConversionStatus.PARTIAL_SUCCESS,
        }:
            action = ConversionPlanAction.CONVERT
            reason = f"retry previous {previous.status.value}"
        elif previous.conversion_fingerprint != conversion_fingerprint:
            action = ConversionPlanAction.CONVERT
            reason = "conversion fingerprint changed"
        elif previous.representative_path != group.representative.relative_path:
            action = ConversionPlanAction.CONVERT
            reason = "representative source path changed"
        elif previous.output_relative_path != expected_output:
            action = ConversionPlanAction.CONVERT
            reason = "converted output path changed"
        elif not output_exists(expected_output):
            action = ConversionPlanAction.CONVERT
            reason = "converted output is missing"
        else:
            action = ConversionPlanAction.REUSE
            reason = (
                "source aliases changed; refresh manifest only"
                if previous.source_paths != group.source_paths
                else "source and conversion settings are unchanged"
            )
        planned.append(
            PlannedConversion(
                source_group=group,
                action=action,
                reason=reason,
                output_relative_path=expected_output,
                previous_record=previous,
            )
        )

    removed = tuple(
        record
        for index, record in enumerate(previous_records)
        if index not in used_previous
    )
    return ConversionPlan(
        documents=tuple(planned),
        removed_records=removed,
        orphan_paths=tuple(sorted(orphan_paths)),
    )


class ArtifactStore:
    """Manage staging and current Markdown under one converted-docs volume."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.staging_root = self.root / "staging"
        self.current_root = self.root / "current"

    def prepare(self) -> None:
        self.staging_root.mkdir(parents=True, exist_ok=True)
        self.current_root.mkdir(parents=True, exist_ok=True)

    def stage_markdown(
        self,
        *,
        run_id: str,
        relative_path: str,
        content: str,
        max_output_size: int | None = None,
    ) -> Path:
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise UnsafePathError(f"unsafe run ID: {run_id!r}")
        output_size = len(content.encode("utf-8"))
        if max_output_size is not None and output_size > max_output_size:
            raise ValueError(
                f"converted Markdown size {output_size} exceeds limit {max_output_size}"
            )
        run_root = self.staging_root / run_id
        destination = _resolve_managed_path(run_root, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return destination

    def publish(self, staged_path: Path, relative_path: str) -> Path:
        resolved_staged = staged_path.resolve()
        try:
            resolved_staged.relative_to(self.staging_root.resolve())
        except ValueError as error:
            raise UnsafePathError("staged path is outside the staging root") from error
        if not resolved_staged.is_file():
            raise FileNotFoundError(resolved_staged)
        destination = _resolve_managed_path(self.current_root, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(resolved_staged, destination)
        return destination

    def discard_staged(self, staged_path: Path) -> None:
        resolved_staged = staged_path.resolve()
        try:
            resolved_staged.relative_to(self.staging_root.resolve())
        except ValueError as error:
            raise UnsafePathError("staged path is outside the staging root") from error
        resolved_staged.unlink(missing_ok=True)

    def current_path(self, relative_path: str) -> Path:
        return _resolve_managed_path(self.current_root, relative_path)

    def current_artifact_paths(self) -> tuple[str, ...]:
        """List every published Markdown artifact as a relative POSIX path.

        Only the ``.md`` files this store creates are reported. Anything else a
        human left under ``current/`` is not ours to enumerate or delete.
        """
        if not self.current_root.is_dir():
            return ()
        paths: list[str] = []
        for root, directories, files in os.walk(self.current_root, followlinks=False):
            directories[:] = sorted(
                name
                for name in directories
                if not (Path(root) / name).is_symlink()
            )
            for filename in sorted(files):
                path = Path(root) / filename
                if path.is_symlink() or path.suffix.lower() != ".md":
                    continue
                paths.append(path.relative_to(self.current_root).as_posix())
        return tuple(sorted(paths))

    def prune_empty_directories(self) -> tuple[str, ...]:
        """Drop directories left behind by removed artifacts, deepest first."""
        if not self.current_root.is_dir():
            return ()
        removed: list[str] = []
        for root, _directories, _files in os.walk(self.current_root, topdown=False):
            path = Path(root)
            if path == self.current_root or path.is_symlink():
                continue
            try:
                next(path.iterdir())
            except StopIteration:
                path.rmdir()
                removed.append(path.relative_to(self.current_root).as_posix())
            except OSError:
                continue
        return tuple(removed)

    def remove_current(self, relative_path: str) -> bool:
        path = self.current_path(relative_path)
        if not path.exists():
            return False
        if not path.is_file():
            raise RuntimeError(f"converted artifact is not a regular file: {path}")
        path.unlink()
        return True

    def cleanup_staging(
        self,
        *,
        older_than_seconds: int,
        now: float | None = None,
    ) -> tuple[str, ...]:
        stale = self.stale_staging_runs(
            older_than_seconds=older_than_seconds,
            now=now,
        )
        removed: list[str] = []
        for name in stale:
            child = self.staging_root / name
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
            elif child.exists():
                raise RuntimeError(f"unsupported staging entry: {child}")
            removed.append(name)
        return tuple(removed)

    def stale_staging_runs(
        self,
        *,
        older_than_seconds: int,
        now: float | None = None,
    ) -> tuple[str, ...]:
        if older_than_seconds < 0:
            raise ValueError("older_than_seconds must not be negative")
        if not self.staging_root.exists():
            return ()
        current_time = time.time() if now is None else now
        stale: list[str] = []
        for child in sorted(self.staging_root.iterdir(), key=lambda item: item.name):
            try:
                age = current_time - child.stat(follow_symlinks=False).st_mtime
            except FileNotFoundError:
                continue
            if age < older_than_seconds:
                continue
            stale.append(child.name)
        return tuple(stale)

    def remove_staging_run(self, run_id: str) -> bool:
        if not _RUN_ID_PATTERN.fullmatch(run_id):
            raise UnsafePathError(f"unsafe run ID: {run_id!r}")
        run_root = self.staging_root / run_id
        if not run_root.exists():
            return False
        if run_root.is_symlink() or not run_root.is_dir():
            raise RuntimeError(f"staging run is not a directory: {run_root}")
        shutil.rmtree(run_root)
        return True


@dataclass(frozen=True)
class IngestionEvent:
    timestamp: str
    event: str
    fields: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "timestamp": self.timestamp,
            "event": self.event,
            **self.fields,
        }


@dataclass(frozen=True)
class IngestionRunResult:
    status: IngestionRunStatus
    plan: ConversionPlan
    manifest: ConversionManifest
    events: tuple[IngestionEvent, ...]
    published_paths: tuple[str, ...]
    unchanged_paths: tuple[str, ...]
    removed_paths: tuple[str, ...]
    deferred_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PendingPublication:
    staged_path: Path
    relative_path: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _attempt_failure_record(
    *,
    planned: PlannedConversion,
    attempt: ConversionAttempt,
    decision: ConversionDecision,
    conversion_fingerprint: str,
    attempted_at: str,
) -> ConversionRecord:
    group = planned.source_group
    source = group.representative
    previous = planned.previous_record
    retaining = decision.action is ConversionAction.RETAIN_PREVIOUS
    served_hash = None
    if retaining and previous is not None:
        served_hash = (
            previous.served_source_sha256
            if previous.serving_previous_version
            else previous.source_sha256
        )
    return ConversionRecord(
        source_sha256=group.sha256,
        source_paths=group.source_paths,
        representative_path=source.relative_path,
        source_size=source.size,
        source_mtime_ns=source.mtime_ns,
        conversion_fingerprint=conversion_fingerprint,
        status=ConversionStatus.FAILURE,
        attempted_at=attempted_at,
        converted_at=previous.converted_at if retaining and previous else None,
        task_id=attempt.task_id,
        retry_count=attempt.retry_count,
        output_relative_path=(
            previous.output_relative_path if retaining and previous else None
        ),
        output_sha256=previous.output_sha256 if retaining and previous else None,
        missing_pages=attempt.missing_pages,
        missing_pages_known=attempt.missing_pages_known,
        errors=attempt.errors,
        warnings=attempt.warnings,
        processing_time_seconds=attempt.processing_time_seconds,
        serving_previous_version=retaining,
        served_source_sha256=served_hash,
        mrag_document_id=(
            previous.mrag_document_id if retaining and previous else None
        ),
    )


def run_conversion_pipeline(
    *,
    source_groups: Sequence[SourceGroup],
    source_issues: Sequence[SourceIssue] = (),
    previous_manifest: ConversionManifest | None,
    artifact_store: ArtifactStore,
    client: DoclingClient,
    settings: DoclingSettings,
    conversion_fingerprint: str,
    converter_version: str,
    run_id: str,
    manifest_path: Path | None = None,
    deletion_guard: Callable[[int, int], str | None] | None = None,
    force: bool = False,
    now: Callable[[], str] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
) -> IngestionRunResult:
    """Execute the conversion phase without invoking mrag.

    Document-level failures are committed to the manifest. Exceptions raised by
    the client are systemic and abort before any staged artifact is published.

    ``deletion_guard`` receives the planned removal count and the previous
    document count, and returns a reason when the removal is too large to apply
    unattended. It is evaluated before the first conversion so that a source
    tree that went missing aborts the run without destroying anything.
    """
    artifact_store.prepare()
    target_manifest = (
        artifact_store.root / "conversion-manifest.json"
        if manifest_path is None
        else manifest_path
    )
    current_paths = artifact_store.current_artifact_paths()
    plan = build_conversion_plan(
        source_groups,
        previous_manifest,
        conversion_fingerprint=conversion_fingerprint,
        output_exists=lambda relative: artifact_store.current_path(relative).is_file(),
        orphan_paths=find_orphan_artifacts(
            current_paths,
            source_groups,
            previous_manifest,
        ),
        force=force,
    )
    events: list[IngestionEvent] = []

    def record_event(event: str, **fields: object) -> None:
        events.append(IngestionEvent(timestamp=now(), event=event, fields=fields))

    record_event("conversion_plan", source_issues=len(source_issues), **plan.summary())

    # Evaluated here, before the first Docling call, so that an unreadable or
    # unmounted source tree cannot delete a whole generation of artifacts and
    # force a full re-conversion. mrag runs its own guard afterwards, but by
    # then these artifacts would already be gone.
    if deletion_guard is not None and plan.removal_count():
        # A lost manifest reports no previous documents, which would turn the
        # percentage rule off exactly when the orphan sweep is at its widest, so
        # the artifacts on disk set the floor for the baseline.
        previous_count = max(
            len(previous_manifest.documents) if previous_manifest is not None else 0,
            len(current_paths),
        )
        guard_reason = deletion_guard(plan.removal_count(), previous_count)
        if guard_reason:
            raise LargeDeletionError(
                "large deletion guard stopped the conversion phase: "
                f"{guard_reason}; review with --dry-run and retry with "
                "--allow-large-delete"
            )

    for issue in source_issues:
        record_event(
            "source_validation_failed",
            relative_path=issue.relative_path,
            code=issue.code,
            message=issue.message,
        )
    records: list[ConversionRecord] = []
    pending: list[_PendingPublication] = []
    unchanged_paths: list[str] = []
    deferred_sources: list[str] = []

    # Documents are submitted ahead of the loop so that up to
    # ``batch_concurrency`` conversions are in flight, but every decision,
    # staging write and manifest record still happens on this thread in plan
    # order. The event log and the manifest therefore stay identical to a
    # sequential run whatever the concurrency is set to.
    window = max(1, settings.batch_concurrency)
    deadline = (
        None
        if settings.run_timeout_seconds is None
        else monotonic() + settings.run_timeout_seconds
    )
    in_flight: dict[int, Future[ConversionAttempt]] = {}
    attempted_at_by_index: dict[int, str] = {}
    deferred_indexes: set[int] = set()
    submit_cursor = 0
    executor = ThreadPoolExecutor(
        max_workers=window,
        thread_name_prefix="docling-convert",
    )

    def fill_window() -> None:
        """Keep the submission window full, walking the plan in order."""
        nonlocal submit_cursor
        while submit_cursor < len(plan.documents) and len(in_flight) < window:
            index = submit_cursor
            item = plan.documents[index]
            submit_cursor += 1
            if item.action is not ConversionPlanAction.CONVERT:
                continue
            if deadline is not None and monotonic() >= deadline:
                # The budget is checked before submitting rather than while
                # waiting, so a document is either given its full conversion
                # timeout or not started at all.
                deferred_indexes.add(index)
                continue
            attempted_at_by_index[index] = now()
            record_event(
                "conversion_started",
                source_path=item.source_group.representative.relative_path,
                source_sha256=item.source_group.sha256,
                reason=item.reason,
            )
            in_flight[index] = executor.submit(
                client.convert,
                item.source_group.representative,
                settings,
            )

    try:
        for index, planned in enumerate(plan.documents):
            fill_window()
            group = planned.source_group
            source = group.representative
            previous = planned.previous_record
            if planned.action is ConversionPlanAction.REUSE:
                if previous is None:
                    raise RuntimeError("reuse plan is missing its previous record")
                records.append(
                    replace(
                        previous,
                        source_paths=group.source_paths,
                        representative_path=source.relative_path,
                        source_size=source.size,
                        source_mtime_ns=source.mtime_ns,
                    )
                )
                record_event(
                    "conversion_reused",
                    source_path=source.relative_path,
                    source_sha256=group.sha256,
                    output_relative_path=planned.output_relative_path,
                    reason=planned.reason,
                )
                continue

            if index in deferred_indexes:
                deferred_sources.append(source.relative_path)
                if previous is not None:
                    # Carried forward untouched. The record still describes the
                    # source that produced the published Markdown, so the next
                    # run re-derives this very conversion and retries it, and
                    # the artifact stays out of the removal set meanwhile.
                    records.append(previous)
                record_event(
                    "conversion_deferred",
                    source_path=source.relative_path,
                    source_sha256=group.sha256,
                    reason="run time budget exhausted",
                    retained_output=(
                        previous.output_relative_path if previous is not None else None
                    ),
                )
                continue

            attempted_at = attempted_at_by_index[index]
            attempt = in_flight.pop(index).result()
            decision = decide_conversion(
                attempt,
                previous_record_exists=previous is not None,
                previous_output_exists=bool(
                    previous
                    and previous.output_relative_path
                    and artifact_store.current_path(
                        previous.output_relative_path
                    ).is_file()
                ),
            )

            published_markdown: str | None = None
            if decision.action is ConversionAction.PUBLISH:
                try:
                    published_markdown = build_published_markdown(
                        markdown=attempt.markdown or "",
                        source_path=source.relative_path,
                        source_sha256=group.sha256,
                        status=attempt.status,
                        converter_version=converter_version,
                        max_output_size=settings.max_output_size,
                    )
                except ValueError as error:
                    conversion_error = ConversionError.normalized(
                        component="conversion_output",
                        code="invalid_markdown",
                        message=error,
                        secrets=(settings.api_key,),
                    )
                    attempt = replace(
                        attempt,
                        status=ConversionStatus.FAILURE,
                        markdown=None,
                        errors=(*attempt.errors, conversion_error),
                    )
                    decision = decide_conversion(
                        attempt,
                        previous_record_exists=previous is not None,
                        previous_output_exists=bool(
                            previous
                            and previous.output_relative_path
                            and artifact_store.current_path(
                                previous.output_relative_path
                            ).is_file()
                        ),
                    )

            if decision.action is not ConversionAction.PUBLISH:
                failure_record = _attempt_failure_record(
                    planned=planned,
                    attempt=attempt,
                    decision=decision,
                    conversion_fingerprint=conversion_fingerprint,
                    attempted_at=attempted_at,
                )
                records.append(failure_record)
                record_event(
                    (
                        "conversion_failed_previous_retained"
                        if failure_record.serving_previous_version
                        else "conversion_failed"
                    ),
                    source_path=source.relative_path,
                    source_sha256=group.sha256,
                    reason=decision.reason,
                    task_id=attempt.task_id,
                )
                continue

            if published_markdown is None:
                raise RuntimeError("publish decision has no generated Markdown")
            staged = artifact_store.stage_markdown(
                run_id=run_id,
                relative_path=planned.output_relative_path,
                content=published_markdown,
                max_output_size=settings.max_output_size,
            )
            output_sha256 = sha256_text(published_markdown)
            output_unchanged = bool(
                previous
                and previous.output_relative_path == planned.output_relative_path
                and previous.output_sha256 == output_sha256
                and artifact_store.current_path(planned.output_relative_path).is_file()
            )
            if output_unchanged:
                artifact_store.discard_staged(staged)
                unchanged_paths.append(planned.output_relative_path)
                record_event(
                    "conversion_output_unchanged",
                    source_path=source.relative_path,
                    source_sha256=group.sha256,
                    output_relative_path=planned.output_relative_path,
                )
            else:
                pending.append(
                    _PendingPublication(
                        staged_path=staged,
                        relative_path=planned.output_relative_path,
                    )
                )
            records.append(
                ConversionRecord(
                    source_sha256=group.sha256,
                    source_paths=group.source_paths,
                    representative_path=source.relative_path,
                    source_size=source.size,
                    source_mtime_ns=source.mtime_ns,
                    conversion_fingerprint=conversion_fingerprint,
                    status=attempt.status,
                    attempted_at=attempted_at,
                    converted_at=now(),
                    task_id=attempt.task_id,
                    retry_count=attempt.retry_count,
                    output_relative_path=planned.output_relative_path,
                    output_sha256=output_sha256,
                    missing_pages=attempt.missing_pages,
                    missing_pages_known=attempt.missing_pages_known,
                    errors=attempt.errors,
                    warnings=attempt.warnings,
                    processing_time_seconds=attempt.processing_time_seconds,
                    serving_previous_version=False,
                    served_source_sha256=None,
                    mrag_document_id=(
                        previous.mrag_document_id
                        if output_unchanged and previous
                        else None
                    ),
                )
            )
            record_event(
                (
                    "conversion_partial_success"
                    if attempt.status is ConversionStatus.PARTIAL_SUCCESS
                    else "conversion_completed"
                ),
                source_path=source.relative_path,
                source_sha256=group.sha256,
                output_relative_path=planned.output_relative_path,
                output_sha256=output_sha256,
                missing_pages=attempt.missing_pages,
                missing_pages_known=attempt.missing_pages_known,
                warning_count=len(attempt.warnings),
                error_count=len(attempt.errors),
                task_id=attempt.task_id,
            )
    finally:
        # Nothing is published until every document has been processed, so
        # abandoning the conversions still in flight leaves the converted tree
        # exactly as this run found it.
        executor.shutdown(cancel_futures=True)

    published_paths: list[str] = []
    for item in pending:
        artifact_store.publish(item.staged_path, item.relative_path)
        published_paths.append(item.relative_path)
        record_event("conversion_published", output_relative_path=item.relative_path)

    manifest = ConversionManifest(
        conversion_fingerprint=conversion_fingerprint,
        completed_at=now(),
        documents=tuple(sorted(records, key=lambda record: record.representative_path)),
        source_issues=tuple(
            sorted(source_issues, key=lambda issue: issue.relative_path)
        ),
    )
    write_conversion_manifest(target_manifest, manifest)

    desired_paths = {
        record.output_relative_path
        for record in manifest.documents
        if record.output_relative_path is not None
    }
    previous_paths = {
        record.output_relative_path
        for record in (
            previous_manifest.documents if previous_manifest is not None else ()
        )
        if record.output_relative_path is not None
    }
    # Deleted only now that the manifest naming the survivors is on disk: a
    # crash between the two leaves extra artifacts, which the orphan sweep
    # collects on the next run, rather than artifacts no manifest can explain.
    removed_paths: list[str] = []
    for relative_path in sorted(previous_paths - desired_paths):
        if artifact_store.remove_current(relative_path):
            removed_paths.append(relative_path)
            record_event(
                "conversion_removed",
                output_relative_path=relative_path,
                reason="source_removed",
            )
    for relative_path in plan.orphan_paths:
        if relative_path in desired_paths:
            continue
        if artifact_store.remove_current(relative_path):
            removed_paths.append(relative_path)
            record_event(
                "conversion_removed",
                output_relative_path=relative_path,
                reason="orphan_artifact",
            )
    if removed_paths:
        artifact_store.prune_empty_directories()
    artifact_store.remove_staging_run(run_id)

    has_errors = (
        any(
            record.status is not ConversionStatus.SUCCESS
            for record in manifest.documents
        )
        or bool(manifest.source_issues)
        or bool(deferred_sources)
    )
    status = (
        IngestionRunStatus.PARTIAL_SUCCESS if has_errors else IngestionRunStatus.SUCCESS
    )
    record_event(
        (
            "conversion_run_completed_with_errors"
            if has_errors
            else "conversion_run_completed"
        ),
        status=status.value,
        source_issues=len(manifest.source_issues),
        deferred=len(deferred_sources),
        **manifest.summary(),
    )
    return IngestionRunResult(
        status=status,
        plan=plan,
        manifest=manifest,
        events=tuple(events),
        published_paths=tuple(published_paths),
        unchanged_paths=tuple(unchanged_paths),
        removed_paths=tuple(removed_paths),
        deferred_sources=tuple(deferred_sources),
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
