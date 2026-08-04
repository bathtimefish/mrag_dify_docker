"""HTTP adapter for the pinned docling-serve API."""

from __future__ import annotations

import json
import mimetypes
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

import httpx

# Re-exported from the dependency-free module so that existing importers of this
# adapter keep working while offline tooling can read the same pinned values.
from docling_ingestion import (
    DOCLING_CPU_IMAGE_DIGEST,
    DOCLING_NVIDIA_IMAGE_DIGEST,
    EXPECTED_DOCLING_SERVE_VERSION,
    EXPECTED_DOCLING_VERSION,
    PAGE_BREAK_SENTINEL,
    ConversionAttempt,
    ConversionError,
    ConversionStatus,
    DoclingServiceError,
    DoclingSettings,
    SourceDocument,
    count_pages,
    strip_page_break_sentinel,
)

__all__ = [
    "DOCLING_CPU_IMAGE_DIGEST",
    "DOCLING_NVIDIA_IMAGE_DIGEST",
    "EXPECTED_DOCLING_SERVE_VERSION",
    "EXPECTED_DOCLING_VERSION",
    "DoclingProtocolError",
    "HttpDoclingClient",
]
TERMINAL_STATUSES = {
    "success",
    "partial_success",
    "skipped",
    "failure",
}
PENDING_STATUSES = {"pending", "started"}


class DoclingProtocolError(DoclingServiceError):
    """The server response does not match the pinned API contract."""


class HttpDoclingClient:
    """Convert one document through docling-serve's asynchronous REST API."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        expected_version: str = EXPECTED_DOCLING_SERVE_VERSION,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic
        self._expected_version = expected_version
        self._verified_urls: set[str] = set()

    def convert(
        self,
        source: SourceDocument,
        settings: DoclingSettings,
    ) -> ConversionAttempt:
        client = self._client or httpx.Client(
            timeout=httpx.Timeout(settings.connection_timeout_seconds)
        )
        owns_client = self._client is None
        retry_count = 0
        try:
            retry_count += self._verify_server(client, settings)
            task, retries = self._submit(client, source, settings)
            retry_count += retries
            validation_error = task.get("document_validation_error")
            if isinstance(validation_error, str):
                return ConversionAttempt(
                    status=ConversionStatus.FAILURE,
                    retry_count=retry_count,
                    errors=(
                        ConversionError.normalized(
                            component="docling_api",
                            code="document_validation_failed",
                            message=validation_error,
                            secrets=(settings.api_key,),
                        ),
                    ),
                )
            task_id = _required_string(task, "task_id")
            status = _required_string(task, "task_status")
            deadline = self._monotonic() + settings.conversion_timeout_seconds

            while status in PENDING_STATUSES:
                now = self._monotonic()
                if now >= deadline:
                    return _timeout_attempt(task_id, retry_count)
                wait_seconds = min(
                    settings.poll_interval_seconds,
                    max(0.0, deadline - now),
                )
                if wait_seconds:
                    self._sleep(wait_seconds)
                if self._monotonic() >= deadline:
                    return _timeout_attempt(task_id, retry_count)
                task, retries = self._request_json(
                    client,
                    "GET",
                    f"{settings.serve_url}/v1/status/poll/{task_id}",
                    settings,
                    params={"wait": 0},
                )
                retry_count += retries
                status = _required_string(task, "task_status")

            if status not in TERMINAL_STATUSES:
                raise DoclingProtocolError(
                    f"unknown Docling task status: {status!r}"
                )

            result, retries = self._request_json(
                client,
                "GET",
                f"{settings.serve_url}/v1/result/{task_id}",
                settings,
            )
            retry_count += retries
            return _apply_page_limit(
                _normalize_result(
                    result,
                    task_id=task_id,
                    retry_count=retry_count,
                    api_key=settings.api_key,
                ),
                settings=settings,
                source=source,
            )
        finally:
            if owns_client:
                client.close()

    def _verify_server(
        self,
        client: httpx.Client,
        settings: DoclingSettings,
    ) -> int:
        if settings.serve_url in self._verified_urls:
            return 0
        payload, retries = self._request_json(
            client,
            "GET",
            f"{settings.serve_url}/version",
            settings,
        )
        version = _required_string(payload, "docling-serve")
        if version != self._expected_version:
            raise DoclingProtocolError(
                "unsupported docling-serve version: "
                f"expected {self._expected_version}, got {version}"
            )
        docling_version = _required_string(payload, "docling")
        if docling_version != EXPECTED_DOCLING_VERSION:
            raise DoclingProtocolError(
                "unsupported docling version: "
                f"expected {EXPECTED_DOCLING_VERSION}, got {docling_version}"
            )
        self._verified_urls.add(settings.serve_url)
        return retries

    def _submit(
        self,
        client: httpx.Client,
        source: SourceDocument,
        settings: DoclingSettings,
    ) -> tuple[dict[str, Any], int]:
        content_type = (
            mimetypes.guess_type(source.absolute_path.name)[0]
            or "application/octet-stream"
        )
        data: dict[str, str | list[str]] = {
            "to_formats": [settings.output_format],
            "image_export_mode": settings.image_export_mode,
            "do_ocr": _form_bool(settings.ocr_enabled),
            "table_mode": settings.table_mode,
            "abort_on_error": "false",
            "ocr_lang": list(settings.ocr_languages),
        }
        if settings.max_num_pages is not None:
            # The API has no page cap of its own. Ask for one page more than the
            # limit and count what comes back: exactly limit+1 pages proves the
            # document is over the limit, while fewer proves it is complete.
            data["page_range"] = ["1", str(settings.max_num_pages + 1)]
            data["md_page_break_placeholder"] = PAGE_BREAK_SENTINEL
        optional_fields = {
            "ocr_preset": settings.ocr_preset,
            "pipeline": settings.pipeline,
            "pdf_backend": settings.pdf_backend,
        }
        data.update(
            {
                name: value
                for name, value in optional_fields.items()
                if value is not None
            }
        )
        with source.absolute_path.open("rb") as stream:
            return self._request_json(
                client,
                "POST",
                f"{settings.serve_url}/v1/convert/file/async",
                settings,
                data=data,
                files={
                    "files": (
                        source.absolute_path.name,
                        stream,
                        content_type,
                    )
                },
                document_request=True,
            )

    def _request_json(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        settings: DoclingSettings,
        *,
        document_request: bool = False,
        **kwargs: Any,
    ) -> tuple[dict[str, Any], int]:
        retries = 0
        while True:
            try:
                response = client.request(
                    method,
                    url,
                    headers={"X-Api-Key": settings.api_key},
                    **kwargs,
                )
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                if retries >= settings.max_retries:
                    raise DoclingServiceError(
                        f"Docling connection failed after {retries + 1} attempts"
                    ) from error
                retries += 1
                self._sleep(_backoff_seconds(retries))
                _rewind_files(kwargs.get("files"))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if retries >= settings.max_retries:
                    raise DoclingServiceError(
                        "Docling service returned retryable HTTP "
                        f"{response.status_code} after {retries + 1} attempts"
                    )
                retries += 1
                self._sleep(_retry_delay(response, retries))
                _rewind_files(kwargs.get("files"))
                continue

            if response.status_code in {401, 403}:
                raise DoclingServiceError(
                    f"Docling authentication failed with HTTP {response.status_code}"
                )
            if response.status_code == 422 and document_request:
                return _document_validation_failure(response, settings.api_key), retries
            if response.status_code >= 400:
                raise DoclingServiceError(
                    f"Docling API returned unexpected HTTP {response.status_code}"
                )

            if (
                settings.max_output_size is not None
                and len(response.content) > settings.max_output_size + 1024 * 1024
            ):
                raise DoclingServiceError("Docling response exceeds configured limit")
            try:
                payload = response.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                raise DoclingProtocolError(
                    "Docling returned invalid JSON"
                ) from error
            if not isinstance(payload, dict):
                raise DoclingProtocolError("Docling response must be a JSON object")
            return payload, retries


def _form_bool(value: bool) -> str:
    return "true" if value else "false"


def _required_string(payload: Mapping[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise DoclingProtocolError(f"Docling response is missing string field {name!r}")
    return value


def _backoff_seconds(retry_number: int) -> float:
    return min(2 ** (retry_number - 1), 30)


def _retry_delay(response: httpx.Response, retry_number: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), 60.0)
        except ValueError:
            pass
    return _backoff_seconds(retry_number)


def _rewind_files(files: Any) -> None:
    if not isinstance(files, Mapping):
        return
    for value in files.values():
        if isinstance(value, tuple) and len(value) >= 2:
            stream = value[1]
            if hasattr(stream, "seek"):
                stream.seek(0)


def _document_validation_failure(
    response: httpx.Response,
    api_key: str,
) -> dict[str, Any]:
    message = "Docling rejected the source document"
    try:
        detail = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        detail = None
    if detail is not None:
        serialized = json.dumps(detail, ensure_ascii=False)
        if api_key:
            serialized = serialized.replace(api_key, "[REDACTED]")
        message = f"{message}: {serialized}"
    return {
        "task_id": "",
        "task_status": "failure",
        "document_validation_error": message,
    }


def _timeout_attempt(task_id: str, retry_count: int) -> ConversionAttempt:
    return ConversionAttempt(
        status=ConversionStatus.FAILURE,
        task_id=task_id,
        retry_count=retry_count,
        errors=(
            ConversionError.normalized(
                component="docling_client",
                code="conversion_timeout",
                message="Docling conversion timed out",
            ),
        ),
    )


def _apply_page_limit(
    attempt: ConversionAttempt,
    *,
    settings: DoclingSettings,
    source: SourceDocument,
) -> ConversionAttempt:
    """Reject documents over the page limit and clean up the counting markers.

    Truncating instead would put a silently incomplete document into the
    knowledge base: retrieval would succeed, the citation would look
    authoritative, and nobody asking a question would learn that the rest of the
    document was never ingested. A rejected document is visible in the manifest,
    retried on the next run, and ingested as soon as the limit is raised.
    """
    if settings.max_num_pages is None or attempt.markdown is None:
        return attempt

    pages = count_pages(attempt.markdown)
    if pages > settings.max_num_pages:
        return replace(
            attempt,
            status=ConversionStatus.FAILURE,
            markdown=None,
            errors=(
                *attempt.errors,
                ConversionError.normalized(
                    component="conversion_policy",
                    code="page_limit_exceeded",
                    message=(
                        f"{source.relative_path} has more than "
                        f"{settings.max_num_pages} page(s), the configured "
                        "DOCLING_MAX_NUM_PAGES limit. It was not converted "
                        "rather than being silently truncated. Raise the limit "
                        "or split the document."
                    ),
                    secrets=(settings.api_key,),
                ),
            ),
        )
    return replace(attempt, markdown=strip_page_break_sentinel(attempt.markdown))


def _normalize_result(
    payload: Mapping[str, Any],
    *,
    task_id: str,
    retry_count: int,
    api_key: str,
) -> ConversionAttempt:
    if payload.get("kind") == "TaskFailureResult":
        failure = payload.get("failure")
        if not isinstance(failure, Mapping):
            raise DoclingProtocolError("TaskFailureResult is missing failure details")
        error = ConversionError.normalized(
            component=failure.get("phase", "docling"),
            code=failure.get("category", "task_failure"),
            message=failure.get("message", "Docling task failed"),
            raw_detail=failure.get("details"),
            secrets=(api_key,),
        )
        return ConversionAttempt(
            status=ConversionStatus.FAILURE,
            task_id=task_id,
            retry_count=retry_count,
            errors=(error,),
        )

    raw_status = _required_string(payload, "status")
    try:
        status = ConversionStatus(raw_status)
    except ValueError as error:
        raise DoclingProtocolError(
            f"unknown Docling result status: {raw_status!r}"
        ) from error

    raw_errors = payload.get("errors", [])
    if not isinstance(raw_errors, list):
        raise DoclingProtocolError("Docling errors field must be a list")
    errors = tuple(
        _normalize_error(item, api_key=api_key)
        for item in raw_errors
        if isinstance(item, Mapping)
    )
    if len(errors) != len(raw_errors):
        raise DoclingProtocolError("Docling error entries must be objects")

    document = payload.get("document")
    markdown: str | None = None
    if isinstance(document, Mapping):
        value = document.get("md_content")
        if value is not None and not isinstance(value, str):
            raise DoclingProtocolError("Docling md_content must be a string or null")
        markdown = value
    elif status in {ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS}:
        raise DoclingProtocolError("publishable Docling result is missing document")

    pages = tuple(sorted({error.page for error in errors if error.page is not None}))
    missing_pages_known = bool(errors) and all(
        error.page is not None for error in errors
    )
    processing_time = payload.get("processing_time")
    if processing_time is not None and not isinstance(processing_time, (int, float)):
        raise DoclingProtocolError("Docling processing_time must be numeric or null")

    return ConversionAttempt(
        status=status,
        markdown=markdown,
        task_id=task_id,
        warnings=(
            tuple(error.message for error in errors)
            if status is ConversionStatus.PARTIAL_SUCCESS
            else ()
        ),
        errors=errors,
        missing_pages=pages if pages else None,
        missing_pages_known=missing_pages_known,
        processing_time_seconds=(
            float(processing_time) if processing_time is not None else None
        ),
        retry_count=retry_count,
    )


def _normalize_error(
    payload: Mapping[str, Any],
    *,
    api_key: str,
) -> ConversionError:
    page = payload.get("page_no")
    if page is not None and (not isinstance(page, int) or page <= 0):
        page = None
    return ConversionError.normalized(
        component=payload.get("component_type", "docling"),
        code=payload.get("category", "unknown"),
        message=payload.get("error_message", "Docling conversion error"),
        page=page,
        raw_detail={
            "module_name": payload.get("module_name"),
            "category": payload.get("category"),
        },
        secrets=(api_key,),
    )
