from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]

INGESTION_SPEC = importlib.util.spec_from_file_location(
    "docling_ingestion",
    ROOT / "batch" / "docling_ingestion.py",
)
assert INGESTION_SPEC and INGESTION_SPEC.loader
ingestion = importlib.util.module_from_spec(INGESTION_SPEC)
sys.modules[INGESTION_SPEC.name] = ingestion
INGESTION_SPEC.loader.exec_module(ingestion)

CLIENT_SPEC = importlib.util.spec_from_file_location(
    "docling_http_client",
    ROOT / "batch" / "docling_http_client.py",
)
assert CLIENT_SPEC and CLIENT_SPEC.loader
docling_http = importlib.util.module_from_spec(CLIENT_SPEC)
sys.modules[CLIENT_SPEC.name] = docling_http
CLIENT_SPEC.loader.exec_module(docling_http)


def settings(**overrides: object) -> object:
    values = {
        "serve_url": "http://docling.test",
        "api_key": "test-api-secret",
        "poll_interval_seconds": 1,
        "conversion_timeout_seconds": 60,
        "max_retries": 2,
    }
    values.update(overrides)
    return ingestion.DoclingSettings(**values)


def source(tmp_path: Path) -> object:
    path = tmp_path / "device.txt"
    path.write_text("emergency shutdown", encoding="utf-8")
    return ingestion.SourceDocument(
        relative_path="manuals/device.txt",
        absolute_path=path,
        sha256="source-sha",
        size=path.stat().st_size,
        mtime_ns=path.stat().st_mtime_ns,
    )


def response(status: int, payload: object, **headers: str) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers)


def test_async_partial_success_normalization(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        calls.append(request)
        assert request.headers["X-Api-Key"] == "test-api-secret"
        if request.url.path == "/version":
            return response(
                200, {"docling-serve": "1.28.0", "docling": "2.115.0"}
            )
        if request.url.path == "/v1/convert/file/async":
            body = request.read()
            assert b'device.txt' in body
            assert b'name="to_formats"' in body
            assert b"\r\nmd\r\n" in body
            return response(
                200,
                {
                    "task_id": "task-1",
                    "task_type": "convert",
                    "task_status": "pending",
                },
            )
        if request.url.path == "/v1/status/poll/task-1":
            poll_count += 1
            return response(
                200,
                {
                    "task_id": "task-1",
                    "task_type": "convert",
                    "task_status": "started" if poll_count == 1 else "partial_success",
                },
            )
        if request.url.path == "/v1/result/task-1":
            return response(
                200,
                {
                    "document": {
                        "filename": "device.txt",
                        "md_content": "# Converted",
                    },
                    "status": "partial_success",
                    "errors": [
                        {
                            "component_type": "backend",
                            "module_name": "parser",
                            "error_message": "page could not be converted",
                            "category": "execution",
                            "page_no": 2,
                        }
                    ],
                    "processing_time": 1.25,
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = docling_http.HttpDoclingClient(client=client, sleep=lambda _: None)

    result = adapter.convert(source(tmp_path), settings())

    assert result.status is ingestion.ConversionStatus.PARTIAL_SUCCESS
    assert result.markdown == "# Converted"
    assert result.task_id == "task-1"
    assert result.missing_pages == (2,)
    assert result.missing_pages_known is True
    assert result.warnings == ("page could not be converted",)
    assert result.errors[0].page == 2
    assert result.processing_time_seconds == 1.25
    assert [request.url.path for request in calls].count("/version") == 1


def test_retries_429_and_rewinds_multipart_file(tmp_path: Path) -> None:
    submit_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return response(
                200, {"docling-serve": "1.28.0", "docling": "2.115.0"}
            )
        if request.url.path == "/v1/convert/file/async":
            submit_bodies.append(request.read())
            if len(submit_bodies) == 1:
                return response(429, {"detail": "busy"}, **{"Retry-After": "0"})
            return response(
                200,
                {
                    "task_id": "task-2",
                    "task_type": "convert",
                    "task_status": "success",
                },
            )
        if request.url.path == "/v1/result/task-2":
            return response(
                200,
                {
                    "document": {
                        "filename": "device.txt",
                        "md_content": "converted",
                    },
                    "status": "success",
                    "errors": [],
                    "processing_time": 0.1,
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    result = adapter.convert(source(tmp_path), settings())

    assert result.status is ingestion.ConversionStatus.SUCCESS
    assert result.retry_count == 1
    assert len(submit_bodies) == 2
    assert b"emergency shutdown" in submit_bodies[0]
    assert b"emergency shutdown" in submit_bodies[1]


def test_authentication_failure_is_systemic(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response(401, {"detail": "Unauthorized"})

    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(ingestion.DoclingServiceError, match="authentication"):
        adapter.convert(source(tmp_path), settings())


def test_retries_server_error(tmp_path: Path) -> None:
    submit_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal submit_count
        if request.url.path == "/version":
            return response(
                200, {"docling-serve": "1.28.0", "docling": "2.115.0"}
            )
        if request.url.path == "/v1/convert/file/async":
            submit_count += 1
            if submit_count == 1:
                return response(503, {"detail": "unavailable"})
            return response(
                200,
                {
                    "task_id": "task-503",
                    "task_type": "convert",
                    "task_status": "success",
                },
            )
        return response(
            200,
            {
                "document": {"filename": "device.txt", "md_content": "ok"},
                "status": "success",
                "errors": [],
                "processing_time": 0.1,
            },
        )

    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )

    result = adapter.convert(source(tmp_path), settings())

    assert result.status is ingestion.ConversionStatus.SUCCESS
    assert result.retry_count == 1


def test_document_validation_failure_is_normalized(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return response(
                200, {"docling-serve": "1.28.0", "docling": "2.115.0"}
            )
        return response(
            422,
            {"detail": [{"msg": "invalid test-api-secret document"}]},
        )

    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    result = adapter.convert(source(tmp_path), settings())

    assert result.status is ingestion.ConversionStatus.FAILURE
    assert result.task_id is None
    assert result.errors[0].code == "document_validation_failed"
    assert "test-api-secret" not in result.errors[0].message


def test_timeout_is_a_document_failure(tmp_path: Path) -> None:
    clock = iter([0.0, 0.0, 61.0])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return response(
                200, {"docling-serve": "1.28.0", "docling": "2.115.0"}
            )
        if request.url.path == "/v1/convert/file/async":
            return response(
                200,
                {
                    "task_id": "task-timeout",
                    "task_type": "convert",
                    "task_status": "pending",
                },
            )
        raise AssertionError("poll must not occur after the deadline")

    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        monotonic=lambda: next(clock),
    )
    result = adapter.convert(source(tmp_path), settings())

    assert result.status is ingestion.ConversionStatus.FAILURE
    assert result.errors[0].code == "conversion_timeout"


def test_rejects_unpinned_server_version(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return response(200, {"docling-serve": "1.29.0"})

    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    with pytest.raises(docling_http.DoclingProtocolError, match="unsupported"):
        adapter.convert(source(tmp_path), settings())


def test_failure_result_is_normalized(tmp_path: Path) -> None:
    responses = iter(
        [
            response(
                200, {"docling-serve": "1.28.0", "docling": "2.115.0"}
            ),
            response(
                200,
                {
                    "task_id": "task-3",
                    "task_type": "convert",
                    "task_status": "failure",
                },
            ),
            response(
                200,
                {
                    "kind": "TaskFailureResult",
                    "failure": {
                        "category": "execution",
                        "message": "worker failed",
                        "retryable": False,
                        "phase": "convert",
                        "details": {"reason": "bad document"},
                    },
                },
            ),
        ]
    )
    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: next(responses))
        )
    )

    result = adapter.convert(source(tmp_path), settings())

    assert result.status is ingestion.ConversionStatus.FAILURE
    assert result.errors[0].component == "convert"
    assert result.errors[0].code == "execution"


def test_result_rejects_non_object_json(tmp_path: Path) -> None:
    responses = iter(
        [
            response(
                200, {"docling-serve": "1.28.0", "docling": "2.115.0"}
            ),
            response(
                200,
                {
                    "task_id": "task-4",
                    "task_type": "convert",
                    "task_status": "success",
                },
            ),
            response(200, ["not", "an", "object"]),
        ]
    )
    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: next(responses))
        )
    )

    with pytest.raises(docling_http.DoclingProtocolError, match="JSON object"):
        adapter.convert(source(tmp_path), settings())


def test_result_response_size_limit(tmp_path: Path) -> None:
    responses = iter(
        [
            response(
                200, {"docling-serve": "1.28.0", "docling": "2.115.0"}
            ),
            response(
                200,
                {
                    "task_id": "task-large",
                    "task_type": "convert",
                    "task_status": "success",
                },
            ),
            response(
                200,
                {
                    "document": {
                        "filename": "device.txt",
                        "md_content": "x" * (1024 * 1024 + 2),
                    },
                    "status": "success",
                    "errors": [],
                    "processing_time": 0.1,
                },
            ),
        ]
    )
    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: next(responses))
        )
    )

    with pytest.raises(ingestion.DoclingServiceError, match="exceeds"):
        adapter.convert(source(tmp_path), settings(max_output_size=1))


def _page_limit_adapter(markdown: str) -> object:
    """A stub server returning one document whose page count we control."""
    submitted: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return response(200, {"docling-serve": "1.28.0", "docling": "2.115.0"})
        if request.url.path == "/v1/convert/file/async":
            submitted["body"] = request.read()
            return response(
                200,
                {"task_id": "t", "task_type": "convert", "task_status": "success"},
            )
        if request.url.path == "/v1/status/poll/t":
            return response(
                200,
                {"task_id": "t", "task_type": "convert", "task_status": "success"},
            )
        if request.url.path == "/v1/result/t":
            return response(
                200,
                {
                    "document": {"filename": "device.txt", "md_content": markdown},
                    "status": "success",
                    "errors": [],
                    "processing_time": 0.1,
                },
            )
        raise AssertionError(f"unexpected request: {request.url}")

    adapter = docling_http.HttpDoclingClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _: None,
    )
    return adapter, submitted


def _paged(count: int) -> str:
    sentinel = ingestion.PAGE_BREAK_SENTINEL
    return f"\n\n{sentinel}\n\n".join(f"page {i + 1} body" for i in range(count))


def test_page_limit_requests_one_extra_page_and_asks_for_break_markers(
    tmp_path: Path,
) -> None:
    adapter, submitted = _page_limit_adapter(_paged(2))

    adapter.convert(source(tmp_path), settings(max_num_pages=3))

    body = submitted["body"]
    assert b'name="page_range"' in body
    # Requesting limit+1 is what makes an over-limit document detectable.
    assert b"\r\n1\r\n" in body
    assert b"\r\n4\r\n" in body
    assert ingestion.PAGE_BREAK_SENTINEL.encode() in body


def test_page_limit_is_not_requested_when_unset(tmp_path: Path) -> None:
    adapter, submitted = _page_limit_adapter("# Converted")

    result = adapter.convert(source(tmp_path), settings())

    assert b'name="page_range"' not in submitted["body"]
    assert b"md_page_break_placeholder" not in submitted["body"]
    assert result.status is ingestion.ConversionStatus.SUCCESS


def test_document_over_the_page_limit_fails_instead_of_being_truncated(
    tmp_path: Path,
) -> None:
    # limit+1 pages came back, which proves the document exceeds the limit.
    adapter, _ = _page_limit_adapter(_paged(3))

    result = adapter.convert(source(tmp_path), settings(max_num_pages=2))

    assert result.status is ingestion.ConversionStatus.FAILURE
    assert result.markdown is None
    assert result.errors[-1].code == "page_limit_exceeded"
    assert "DOCLING_MAX_NUM_PAGES" in result.errors[-1].message


def test_document_exactly_at_the_page_limit_is_published(tmp_path: Path) -> None:
    adapter, _ = _page_limit_adapter(_paged(3))

    result = adapter.convert(source(tmp_path), settings(max_num_pages=3))

    assert result.status is ingestion.ConversionStatus.SUCCESS
    # The counting markers must never reach the knowledge base.
    assert ingestion.PAGE_BREAK_SENTINEL not in result.markdown
    assert "page 1 body" in result.markdown
    assert "page 3 body" in result.markdown


def test_page_break_markers_are_stripped_without_mangling_the_body() -> None:
    sentinel = ingestion.PAGE_BREAK_SENTINEL
    markdown = f"# Title\n\nfirst\n\n{sentinel}\n\n## Second\n\nlast"

    stripped = ingestion.strip_page_break_sentinel(markdown)

    assert sentinel not in stripped
    assert stripped == "# Title\n\nfirst\n\n## Second\n\nlast"
    assert ingestion.count_pages(markdown) == 2
    # A format without pagination has no markers and counts as one page.
    assert ingestion.count_pages("plain body") == 1


def test_fixture_matches_observed_success_shape() -> None:
    fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "docling-success-v1.28.0.json").read_text(
            encoding="utf-8"
        )
    )

    result = docling_http._normalize_result(
        fixture,
        task_id="fixture-task",
        retry_count=0,
        api_key="not-present",
    )

    assert result.status is ingestion.ConversionStatus.SUCCESS
    assert "Emergency shutdown procedure" in result.markdown


def _fixture(name: str) -> dict:
    return json.loads(
        (ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8")
    )


def test_fixture_matches_observed_failure_shape() -> None:
    result = docling_http._normalize_result(
        _fixture("docling-failure-v1.28.0.json"),
        task_id="fixture-task",
        retry_count=0,
        api_key="not-present",
    )

    assert result.status is ingestion.ConversionStatus.FAILURE
    assert result.markdown is None
    assert len(result.errors) == 1
    # Observed on v1.28.0: page numbers are absent even for backend failures,
    # so the missing-page set stays unknown rather than being guessed.
    assert result.errors[0].page is None
    assert result.missing_pages_known is False


def test_fixture_matches_observed_skipped_shape() -> None:
    result = docling_http._normalize_result(
        _fixture("docling-skipped-v1.28.0.json"),
        task_id="fixture-task",
        retry_count=0,
        api_key="not-present",
    )

    assert result.status is ingestion.ConversionStatus.SKIPPED
    assert result.markdown is None
    assert result.errors[0].page is None
