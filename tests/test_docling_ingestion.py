from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "docling_ingestion",
    ROOT / "batch" / "docling_ingestion.py",
)
assert SPEC and SPEC.loader
ingestion = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ingestion
SPEC.loader.exec_module(ingestion)


def settings_environment(**overrides: str) -> dict[str, str]:
    values = {
        "DOCLING_SERVE_URL": "http://docling-serve:5001/",
        "DOCLING_SERVE_API_KEY": "test-secret",
        "DOCLING_ACCELERATOR": "cpu",
        "DOCLING_SUPPORTED_EXTENSIONS": ".PDF,.docx,.png",
        "DOCLING_OUTPUT_FORMAT": "md",
        "DOCLING_IMAGE_EXPORT_MODE": "placeholder",
        "DOCLING_OCR_ENABLED": "true",
        "DOCLING_OCR_LANGUAGES": "ja,en",
        "DOCLING_TABLE_MODE": "accurate",
        "DOCLING_CONNECTION_TIMEOUT_SECONDS": "10",
        "DOCLING_CONVERSION_TIMEOUT_SECONDS": "60",
        "DOCLING_POLL_INTERVAL_SECONDS": "1",
        "DOCLING_MAX_RETRIES": "2",
        "DOCLING_BATCH_CONCURRENCY": "1",
    }
    values.update(overrides)
    return values


def source_document(
    path: str = "manuals/device.docx",
    digest: str = "source-hash",
) -> object:
    return ingestion.SourceDocument(
        relative_path=path,
        absolute_path=Path("/source") / path,
        sha256=digest,
        size=10,
        mtime_ns=20,
    )


def conversion_record(**overrides: object) -> object:
    values = {
        "source_sha256": "source-hash",
        "source_paths": ("manuals/device.docx",),
        "representative_path": "manuals/device.docx",
        "source_size": 10,
        "source_mtime_ns": 20,
        "conversion_fingerprint": "sha256:fingerprint",
        "status": ingestion.ConversionStatus.SUCCESS,
        "attempted_at": "2026-07-29T00:00:00Z",
        "converted_at": "2026-07-29T00:00:01Z",
        "task_id": "task-1",
        "retry_count": 1,
        "output_relative_path": "manuals/device.docx.md",
        "output_sha256": "output-hash",
    }
    values.update(overrides)
    return ingestion.ConversionRecord(**values)


def source_group(
    paths: tuple[str, ...] = ("manuals/device.docx",),
    digest: str = "source-hash",
) -> object:
    return ingestion.SourceGroup(
        sha256=digest,
        sources=tuple(source_document(path, digest) for path in paths),
    )


def conversion_manifest(
    *records: object, fingerprint: str = "sha256:fingerprint"
) -> object:
    return ingestion.ConversionManifest(
        conversion_fingerprint=fingerprint,
        completed_at="2026-07-29T00:00:02Z",
        documents=tuple(records),
    )


class TestSettings:
    def test_loads_and_normalizes_environment(self) -> None:
        settings = ingestion.DoclingSettings.from_environment(
            settings_environment(
                DOCLING_ACCELERATOR="nvidia",
                DOCLING_MAX_FILE_SIZE="1000",
                DOCLING_OCR_PRESET="custom",
            )
        )

        assert settings.serve_url == "http://docling-serve:5001"
        assert settings.accelerator == "nvidia"
        assert settings.supported_extensions == (".docx", ".pdf", ".png")
        assert settings.max_file_size == 1000
        assert settings.connection_timeout_seconds == 10
        assert settings.ocr_languages == ("ja", "en")
        assert settings.ocr_preset == "custom"

    @pytest.mark.parametrize(
        ("name", "value", "message"),
        [
            ("DOCLING_SERVE_API_KEY", "CHANGE_ME", "API_KEY"),
            ("DOCLING_ACCELERATOR", "metal", "ACCELERATOR"),
            ("DOCLING_OCR_ENABLED", "maybe", "OCR_ENABLED"),
            ("DOCLING_TABLE_MODE", "maximum", "TABLE_MODE"),
            ("DOCLING_MAX_RETRIES", "0", "MAX_RETRIES"),
            ("DOCLING_SUPPORTED_EXTENSIONS", "pdf", "SUPPORTED_EXTENSIONS"),
        ],
    )
    def test_rejects_invalid_environment(
        self,
        name: str,
        value: str,
        message: str,
    ) -> None:
        with pytest.raises(ingestion.ConfigurationError, match=message):
            ingestion.DoclingSettings.from_environment(
                settings_environment(**{name: value})
            )

    def test_fingerprint_is_deterministic_and_excludes_secret(self) -> None:
        first = ingestion.DoclingSettings.from_environment(
            settings_environment(DOCLING_SERVE_API_KEY="secret-one")
        )
        second = ingestion.DoclingSettings.from_environment(
            settings_environment(DOCLING_SERVE_API_KEY="secret-two")
        )

        first_hash = ingestion.compute_conversion_fingerprint(
            docling_version="1.0.0",
            image_digest="sha256:image",
            api_version="v1",
            settings=first,
        )
        second_hash = ingestion.compute_conversion_fingerprint(
            docling_version="1.0.0",
            image_digest="sha256:image",
            api_version="v1",
            settings=second,
        )

        assert first_hash == second_hash
        assert first_hash.startswith("sha256:")

    def test_content_setting_changes_fingerprint(self) -> None:
        first = ingestion.DoclingSettings.from_environment(settings_environment())
        second = ingestion.DoclingSettings.from_environment(
            settings_environment(DOCLING_TABLE_MODE="fast")
        )

        def fingerprint(settings: object) -> str:
            return ingestion.compute_conversion_fingerprint(
                docling_version="1.0.0",
                image_digest="sha256:image",
                api_version="v1",
                settings=settings,
            )

        assert fingerprint(first) != fingerprint(second)

    def test_runtime_and_admission_settings_do_not_change_fingerprint(self) -> None:
        first = ingestion.DoclingSettings.from_environment(settings_environment())
        second = ingestion.DoclingSettings.from_environment(
            settings_environment(
                DOCLING_ACCELERATOR="nvidia",
                DOCLING_MAX_FILE_SIZE="1000",
                DOCLING_SUPPORTED_EXTENSIONS=".pdf,.docx,.xlsx",
                DOCLING_CONVERSION_TIMEOUT_SECONDS="120",
            )
        )

        def fingerprint(settings: object) -> str:
            return ingestion.compute_conversion_fingerprint(
                docling_version="1.0.0",
                image_digest="sha256:image",
                api_version="v1",
                settings=settings,
            )

        assert fingerprint(first) == fingerprint(second)


class TestSourceScanning:
    def test_scans_all_configured_families_and_collects_issues(
        self,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "source"
        nested = source / "資料"
        nested.mkdir(parents=True)
        (nested / "manual.DOCX").write_bytes(b"docx")
        (nested / "scan.PNG").write_bytes(b"png")
        (source / "empty.pdf").touch()
        (source / "ignored.csv").write_text("a,b", encoding="utf-8")
        (source / ".hidden.pdf").write_bytes(b"hidden")

        result = ingestion.scan_sources(source)

        assert [item.relative_path for item in result.documents] == [
            "資料/manual.DOCX",
            "資料/scan.PNG",
        ]
        assert len(result.issues) == 1
        assert result.issues[0].relative_path == "empty.pdf"
        assert "zero-byte" in result.issues[0].message

    def test_file_over_size_limit_is_a_source_issue(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "large.pdf").write_bytes(b"12345")

        result = ingestion.scan_sources(source, max_file_size=4)

        assert result.documents == ()
        assert result.issues[0].relative_path == "large.pdf"
        assert "exceeds limit 4" in result.issues[0].message

    def test_groups_identical_content_and_chooses_first_path(
        self,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "z.pdf").write_bytes(b"same")
        (source / "a.pdf").write_bytes(b"same")

        groups = ingestion.group_sources(ingestion.scan_sources(source).documents)

        assert len(groups) == 1
        assert groups[0].source_paths == ("a.pdf", "z.pdf")
        assert groups[0].representative.relative_path == "a.pdf"

    def test_recognizes_every_default_extension(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        for index, extension in enumerate(ingestion.DEFAULT_SUPPORTED_EXTENSIONS):
            (source / f"document-{index}{extension.upper()}").write_bytes(
                f"content-{index}".encode()
            )

        result = ingestion.scan_sources(source)

        assert len(result.documents) == len(ingestion.DEFAULT_SUPPORTED_EXTENSIONS)
        assert result.issues == ()

    def test_ignores_file_and_directory_symlinks(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        source.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "secret.pdf"
        target.write_bytes(b"secret")
        (source / "file-link.pdf").symlink_to(target)
        (source / "directory-link").symlink_to(outside, target_is_directory=True)

        result = ingestion.scan_sources(source)

        assert result.documents == ()
        assert result.issues == ()

    def test_changed_file_is_recorded_without_aborting_other_sources(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "source"
        source.mkdir()
        changed = source / "changed.pdf"
        stable = source / "stable.pdf"
        changed.write_bytes(b"changed")
        stable.write_bytes(b"stable")
        original = ingestion._sha256_file_stable

        def fake_hash(path: Path) -> object:
            if path.name == "changed.pdf":
                raise RuntimeError("file changed while hashing")
            return original(path)

        monkeypatch.setattr(ingestion, "_sha256_file_stable", fake_hash)

        result = ingestion.scan_sources(source)

        assert [item.relative_path for item in result.documents] == ["stable.pdf"]
        assert [item.relative_path for item in result.issues] == ["changed.pdf"]


class TestPathsAndMarkdown:
    def test_preserves_relative_path_and_original_extension(self) -> None:
        assert (
            ingestion.converted_relative_path("manuals/device.docx")
            == "manuals/device.docx.md"
        )
        assert (
            ingestion.converted_relative_path("画像/操作パネル.PNG")
            == "画像/操作パネル.PNG.md"
        )
        assert (
            ingestion.converted_relative_path("manuals/a b [rev#1].docx")
            == "manuals/a b [rev#1].docx.md"
        )

    @pytest.mark.parametrize("path", ["../escape.pdf", "/absolute.pdf", ""])
    def test_rejects_unsafe_paths(self, path: str) -> None:
        with pytest.raises(ingestion.UnsafePathError):
            ingestion.converted_relative_path(path)

    def test_adds_source_header_to_partial_markdown(self) -> None:
        result = ingestion.build_published_markdown(
            markdown="# Device\ncontent",
            source_path="manuals/device.docx",
            source_sha256="abc123",
            status=ingestion.ConversionStatus.PARTIAL_SUCCESS,
            converter_version="docling-serve 1.0.0",
        )

        assert "manuals/device.docx" in result
        assert "abc123" in result
        assert "partial_success" in result
        assert "ingestion manifest" in result
        assert result.endswith("# Device\ncontent")

    def test_rejects_empty_markdown(self) -> None:
        with pytest.raises(ValueError, match="empty Markdown"):
            ingestion.build_published_markdown(
                markdown=" \n",
                source_path="device.docx",
                source_sha256="abc123",
                status=ingestion.ConversionStatus.SUCCESS,
                converter_version="docling-serve 1.0.0",
            )

    def test_rejects_markdown_above_output_limit(self) -> None:
        with pytest.raises(ValueError, match="exceeds limit"):
            ingestion.build_published_markdown(
                markdown="# content",
                source_path="device.docx",
                source_sha256="abc123",
                status=ingestion.ConversionStatus.SUCCESS,
                converter_version="docling-serve 1.0.0",
                max_output_size=10,
            )


class TestDiagnostics:
    def test_redacts_secrets_removes_control_characters_and_truncates(self) -> None:
        result = ingestion.sanitize_diagnostic(
            "token=secret\x00 " + ("x" * 100),
            max_chars=40,
            secrets=("secret",),
        )

        assert "secret" not in result
        assert "[REDACTED]" in result
        assert "\x00" not in result
        assert len(result) == 40
        assert result.endswith("...[truncated]")

    def test_normalized_error_is_safe_for_manifest(self) -> None:
        error = ingestion.ConversionError.normalized(
            component="converter",
            code="failed",
            message="api-key-value " + ("x" * 3000),
            raw_detail="api-key-value " + ("y" * 5000),
            secrets=("api-key-value",),
        )

        assert "api-key-value" not in error.message
        assert len(error.message) <= ingestion.MAX_DIAGNOSTIC_MESSAGE_CHARS
        assert error.raw_detail is not None
        assert len(error.raw_detail) <= ingestion.MAX_DIAGNOSTIC_RAW_DETAIL_CHARS

    def test_rejects_unbounded_error_created_without_normalization(self) -> None:
        with pytest.raises(ingestion.ManifestError, match="maximum length"):
            ingestion.ConversionError(
                component="converter",
                code="failed",
                message="x" * (ingestion.MAX_DIAGNOSTIC_MESSAGE_CHARS + 1),
            )


class TestStateTransitions:
    def test_success_and_partial_are_published(self) -> None:
        for status in (
            ingestion.ConversionStatus.SUCCESS,
            ingestion.ConversionStatus.PARTIAL_SUCCESS,
        ):
            decision = ingestion.decide_conversion(
                ingestion.ConversionAttempt(status=status, markdown="# content"),
                previous_record_exists=False,
                previous_output_exists=False,
            )
            assert decision.action is ingestion.ConversionAction.PUBLISH
            assert decision.effective_status is status

    def test_new_failure_is_omitted(self) -> None:
        decision = ingestion.decide_conversion(
            ingestion.ConversionAttempt(status=ingestion.ConversionStatus.FAILURE),
            previous_record_exists=False,
            previous_output_exists=False,
        )
        assert decision.action is ingestion.ConversionAction.OMIT

    def test_updated_failure_retains_previous_output(self) -> None:
        decision = ingestion.decide_conversion(
            ingestion.ConversionAttempt(status=ingestion.ConversionStatus.FAILURE),
            previous_record_exists=True,
            previous_output_exists=True,
        )
        assert decision.action is ingestion.ConversionAction.RETAIN_PREVIOUS

    def test_empty_success_is_treated_as_failure(self) -> None:
        decision = ingestion.decide_conversion(
            ingestion.ConversionAttempt(
                status=ingestion.ConversionStatus.SUCCESS,
                markdown=" ",
            ),
            previous_record_exists=True,
            previous_output_exists=True,
        )
        assert decision.action is ingestion.ConversionAction.RETAIN_PREVIOUS
        assert decision.effective_status is ingestion.ConversionStatus.FAILURE

    def test_mock_client_satisfies_abstract_contract(self) -> None:
        calls: list[str] = []

        class FakeClient:
            def convert(self, source: object, settings: object) -> object:
                calls.append(source.relative_path)
                return ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown="# converted",
                    task_id="task-1",
                )

        client: ingestion.DoclingClient = FakeClient()
        result = client.convert(
            source_document(),
            ingestion.DoclingSettings.from_environment(settings_environment()),
        )

        assert result.status is ingestion.ConversionStatus.SUCCESS
        assert calls == ["manuals/device.docx"]


class TestManifest:
    def test_round_trip_preserves_partial_errors_and_missing_pages(
        self,
        tmp_path: Path,
    ) -> None:
        record = conversion_record(
            status=ingestion.ConversionStatus.PARTIAL_SUCCESS,
            missing_pages=(7,),
            missing_pages_known=True,
            errors=(
                ingestion.ConversionError(
                    component="converter",
                    code="page_failed",
                    message="page conversion failed",
                    page=7,
                ),
            ),
            warnings=("some content omitted",),
        )
        manifest = ingestion.ConversionManifest(
            conversion_fingerprint="sha256:fingerprint",
            completed_at="2026-07-29T00:00:02Z",
            documents=(record,),
        )
        path = tmp_path / "conversion-manifest.json"

        ingestion.write_conversion_manifest(path, manifest)
        loaded = ingestion.load_conversion_manifest(path)

        assert loaded == manifest
        assert loaded.summary() == {
            "success": 0,
            "partial_success": 1,
            "failure": 0,
            "serving_previous_version": 0,
        }
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["documents"][0]["missing_pages"] == [7]

    def test_records_unknown_missing_pages_without_guessing(self) -> None:
        record = conversion_record(
            status=ingestion.ConversionStatus.PARTIAL_SUCCESS,
            missing_pages=None,
            missing_pages_known=False,
        )
        assert record.to_dict()["missing_pages"] is None

    def test_requires_served_hash_when_retaining_previous(self) -> None:
        with pytest.raises(ingestion.ManifestError, match="served_source_sha256"):
            conversion_record(
                status=ingestion.ConversionStatus.FAILURE,
                serving_previous_version=True,
            )

    def test_rejects_unknown_manifest_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "conversion-manifest.json"
        path.write_text(
            '{"schema_version":999,"documents":[]}',
            encoding="utf-8",
        )
        with pytest.raises(ingestion.ManifestError, match="unsupported"):
            ingestion.load_conversion_manifest(path)

    def test_failed_atomic_replace_preserves_previous_manifest(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = tmp_path / "conversion-manifest.json"
        path.write_text("previous manifest\n", encoding="utf-8")
        manifest = conversion_manifest(conversion_record())

        def fail_replace(source: object, destination: object) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr(ingestion.os, "replace", fail_replace)

        with pytest.raises(OSError, match="simulated"):
            ingestion.write_conversion_manifest(path, manifest)

        assert path.read_text(encoding="utf-8") == "previous manifest\n"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_corrupt_manifest_does_not_modify_existing_artifact(
        self,
        tmp_path: Path,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        artifact = store.current_path("manuals/device.docx.md")
        artifact.parent.mkdir(parents=True)
        artifact.write_text("# previous", encoding="utf-8")
        manifest_path = store.root / "conversion-manifest.json"
        manifest_path.write_text("{broken", encoding="utf-8")

        with pytest.raises(ingestion.ManifestError, match="could not read"):
            ingestion.load_conversion_manifest(manifest_path)

        assert artifact.read_text(encoding="utf-8") == "# previous"


class TestConversionPlan:
    def build_plan(
        self,
        groups: tuple[object, ...],
        manifest: object | None = None,
        *,
        existing_outputs: tuple[str, ...] = ("manuals/device.docx.md",),
        fingerprint: str = "sha256:fingerprint",
    ) -> object:
        return ingestion.build_conversion_plan(
            groups,
            manifest,
            conversion_fingerprint=fingerprint,
            output_exists=lambda path: path in existing_outputs,
        )

    def test_new_source_is_converted(self) -> None:
        plan = self.build_plan((source_group(),))

        assert plan.summary() == {
            "convert": 1,
            "reuse": 0,
            "remove": 0,
            "orphan": 0,
        }
        assert plan.documents[0].reason == "new source"

    def test_unchanged_source_is_reused(self) -> None:
        plan = self.build_plan(
            (source_group(),),
            conversion_manifest(conversion_record()),
        )

        assert plan.summary() == {
            "convert": 0,
            "reuse": 1,
            "remove": 0,
            "orphan": 0,
        }

    def test_alias_change_refreshes_manifest_without_reconversion(self) -> None:
        current_group = source_group(("manuals/device.docx", "mirror/device.docx"))
        plan = self.build_plan(
            (current_group,),
            conversion_manifest(conversion_record()),
        )

        assert plan.documents[0].action is ingestion.ConversionPlanAction.REUSE
        assert "refresh manifest" in plan.documents[0].reason

    @pytest.mark.parametrize(
        ("manifest", "fingerprint", "existing_outputs", "reason"),
        [
            (
                conversion_manifest(conversion_record()),
                "sha256:new-fingerprint",
                ("manuals/device.docx.md",),
                "fingerprint changed",
            ),
            (
                conversion_manifest(conversion_record()),
                "sha256:fingerprint",
                (),
                "output is missing",
            ),
            (
                conversion_manifest(
                    conversion_record(status=ingestion.ConversionStatus.FAILURE)
                ),
                "sha256:fingerprint",
                ("manuals/device.docx.md",),
                "retry previous failure",
            ),
            (
                conversion_manifest(
                    conversion_record(
                        status=ingestion.ConversionStatus.FAILURE,
                        serving_previous_version=True,
                        served_source_sha256="old-source-hash",
                    )
                ),
                "sha256:fingerprint",
                ("manuals/device.docx.md",),
                "retry failed update",
            ),
        ],
    )
    def test_reconversion_conditions(
        self,
        manifest: object,
        fingerprint: str,
        existing_outputs: tuple[str, ...],
        reason: str,
    ) -> None:
        plan = self.build_plan(
            (source_group(),),
            manifest,
            fingerprint=fingerprint,
            existing_outputs=existing_outputs,
        )

        assert plan.documents[0].action is ingestion.ConversionPlanAction.CONVERT
        assert reason in plan.documents[0].reason

    def test_same_hash_rename_reconverts_header_and_output_path(self) -> None:
        renamed = source_group(("renamed/device.docx",))
        plan = self.build_plan(
            (renamed,),
            conversion_manifest(conversion_record()),
            existing_outputs=("renamed/device.docx.md",),
        )

        assert plan.documents[0].action is ingestion.ConversionPlanAction.CONVERT
        assert "representative source path changed" == plan.documents[0].reason

    def test_content_update_is_matched_by_path_for_previous_fallback(self) -> None:
        updated = source_group(digest="new-source-hash")
        previous = conversion_record()
        plan = self.build_plan(
            (updated,),
            conversion_manifest(previous),
            existing_outputs=("manuals/device.docx.md",),
        )

        item = plan.documents[0]
        assert item.action is ingestion.ConversionPlanAction.CONVERT
        assert item.reason == "source content changed"
        assert item.previous_record is previous
        assert plan.removed_records == ()

    def test_deleted_source_is_removed(self) -> None:
        previous = conversion_record()
        plan = self.build_plan((), conversion_manifest(previous))

        assert plan.summary() == {
            "convert": 0,
            "reuse": 0,
            "remove": 1,
            "orphan": 0,
        }
        assert plan.removed_records == (previous,)

    def test_duplicate_group_split_assigns_previous_to_unchanged_hash(self) -> None:
        previous = conversion_record(
            source_paths=("a.pdf", "b.pdf"),
            representative_path="a.pdf",
            output_relative_path="a.pdf.md",
        )
        unchanged = source_group(("a.pdf",), digest="source-hash")
        changed = source_group(("b.pdf",), digest="new-hash")
        plan = self.build_plan(
            (unchanged, changed),
            conversion_manifest(previous),
            existing_outputs=("a.pdf.md",),
        )

        by_hash = {item.source_group.sha256: item for item in plan.documents}
        assert by_hash["source-hash"].previous_record is previous
        assert by_hash["new-hash"].previous_record is None


class TestArtifactStore:
    def test_stages_and_atomically_publishes_markdown(self, tmp_path: Path) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        staged = store.stage_markdown(
            run_id="run-1",
            relative_path="manuals/device.docx.md",
            content="# converted",
        )

        published = store.publish(staged, "manuals/device.docx.md")

        assert published.read_text(encoding="utf-8") == "# converted"
        assert not staged.exists()
        assert store.remove_current("manuals/device.docx.md") is True
        assert store.remove_current("manuals/device.docx.md") is False

    def test_rejects_publish_from_outside_staging(self, tmp_path: Path) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        outside = tmp_path / "outside.md"
        outside.write_text("# outside", encoding="utf-8")

        with pytest.raises(ingestion.UnsafePathError):
            store.publish(outside, "outside.md")

    def test_rejects_stage_output_above_limit(self, tmp_path: Path) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()

        with pytest.raises(ValueError, match="exceeds limit"):
            store.stage_markdown(
                run_id="run-1",
                relative_path="large.md",
                content="large output",
                max_output_size=5,
            )

        assert list(store.staging_root.rglob("*")) == []

    def test_failed_stage_replace_cleans_temporary_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()

        def fail_replace(source: object, destination: object) -> None:
            raise OSError("simulated replace failure")

        monkeypatch.setattr(ingestion.os, "replace", fail_replace)

        with pytest.raises(OSError, match="simulated"):
            store.stage_markdown(
                run_id="run-1",
                relative_path="manuals/device.docx.md",
                content="# converted",
            )

        assert not store.current_path("manuals/device.docx.md").exists()
        assert list(store.staging_root.rglob("*.tmp")) == []

    def test_cleanup_removes_only_expired_staging_runs(
        self,
        tmp_path: Path,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        old = store.staging_root / "old-run"
        recent = store.staging_root / "recent-run"
        old.mkdir()
        recent.mkdir()
        (old / "partial.md").write_text("old", encoding="utf-8")
        (recent / "partial.md").write_text("recent", encoding="utf-8")
        now = time.time()
        os.utime(old, (now - 3600, now - 3600))
        os.utime(recent, (now, now))

        removed = store.cleanup_staging(
            older_than_seconds=60,
            now=now,
        )

        assert removed == ("old-run",)
        assert not old.exists()
        assert recent.exists()

    def test_lists_published_markdown_and_prunes_emptied_directories(
        self,
        tmp_path: Path,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        for relative in ("top.docx.md", "nested/deep/inner.pdf.md"):
            path = store.current_path(relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# content", encoding="utf-8")
        # Not something this store publishes, so it is neither listed nor swept.
        (store.current_root / "notes.txt").write_text("keep me", encoding="utf-8")

        assert store.current_artifact_paths() == (
            "nested/deep/inner.pdf.md",
            "top.docx.md",
        )

        store.remove_current("nested/deep/inner.pdf.md")
        pruned = store.prune_empty_directories()

        assert pruned == ("nested/deep", "nested")
        assert not (store.current_root / "nested").exists()
        assert (store.current_root / "notes.txt").is_file()
        assert store.current_artifact_paths() == ("top.docx.md",)


class FakeDoclingClient:
    def __init__(self, results: dict[str, object]) -> None:
        self.results = results
        self.calls: list[str] = []

    def convert(self, source: object, settings: object) -> object:
        self.calls.append(source.relative_path)
        result = self.results[source.relative_path]
        if isinstance(result, BaseException):
            raise result
        return result


class TestConversionPipeline:
    def settings(self, **overrides: str) -> object:
        return ingestion.DoclingSettings.from_environment(
            settings_environment(**overrides)
        )

    def run(
        self,
        *,
        tmp_path: Path,
        groups: tuple[object, ...],
        client: object,
        source_issues: tuple[object, ...] = (),
        previous_manifest: object | None = None,
        fingerprint: str = "sha256:fingerprint",
        run_id: str = "run-1",
        deletion_guard: object | None = None,
        monotonic: object | None = None,
        **settings_overrides: str,
    ) -> object:
        extra = {} if monotonic is None else {"monotonic": monotonic}
        return ingestion.run_conversion_pipeline(
            source_groups=groups,
            source_issues=source_issues,
            previous_manifest=previous_manifest,
            artifact_store=ingestion.ArtifactStore(tmp_path / "converted"),
            client=client,
            settings=self.settings(**settings_overrides),
            conversion_fingerprint=fingerprint,
            converter_version="docling-serve test",
            run_id=run_id,
            deletion_guard=deletion_guard,
            now=lambda: "2026-07-29T00:00:00Z",
            **extra,
        )

    def test_force_reconverts_unchanged_sources_without_widening_removals(
        self,
        tmp_path: Path,
    ) -> None:
        previous = conversion_manifest(
            conversion_record(
                source_sha256="hash-a",
                source_paths=("a.docx",),
                representative_path="a.docx",
                output_relative_path="a.docx.md",
            ),
            conversion_record(
                source_sha256="hash-b",
                source_paths=("b.docx",),
                representative_path="b.docx",
                output_relative_path="b.docx.md",
            ),
        )
        groups = (source_group(("a.docx",), "hash-a"),)

        normal = ingestion.build_conversion_plan(
            groups,
            previous,
            conversion_fingerprint="sha256:fingerprint",
            output_exists=lambda relative: True,
        )
        forced = ingestion.build_conversion_plan(
            groups,
            previous,
            conversion_fingerprint="sha256:fingerprint",
            output_exists=lambda relative: True,
            force=True,
        )

        assert normal.summary()["convert"] == 0
        assert forced.summary()["convert"] == 1
        assert forced.documents[0].reason == "forced re-conversion"
        # A forced run must not delete more than a normal one would.
        assert forced.summary()["remove"] == normal.summary()["remove"] == 1

    def test_deletion_guard_aborts_before_converting_or_removing_anything(
        self,
        tmp_path: Path,
    ) -> None:
        # Every previous document disappeared from the source tree, which is what
        # an unmounted kbs_docs looks like.
        previous = conversion_manifest(
            *(
                conversion_record(
                    source_sha256=f"hash-{name}",
                    source_paths=(f"{name}.docx",),
                    representative_path=f"{name}.docx",
                    output_relative_path=f"{name}.docx.md",
                )
                for name in ("a", "b", "c")
            )
        )
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        for name in ("a", "b", "c"):
            path = store.current_path(f"{name}.docx.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {name}", encoding="utf-8")

        client = FakeDoclingClient({})
        with pytest.raises(ingestion.LargeDeletionError, match="deletion guard"):
            self.run(
                tmp_path=tmp_path,
                groups=(),
                client=client,
                previous_manifest=previous,
                deletion_guard=lambda removals, previous_count: (
                    f"removal count {removals} of {previous_count} reached limit"
                ),
            )

        assert client.calls == []
        for name in ("a", "b", "c"):
            assert store.current_path(f"{name}.docx.md").is_file()

    def test_deletion_guard_receives_counts_and_allows_small_removals(
        self,
        tmp_path: Path,
    ) -> None:
        previous = conversion_manifest(
            conversion_record(
                source_sha256="hash-a",
                source_paths=("a.docx",),
                representative_path="a.docx",
                output_relative_path="a.docx.md",
            ),
            conversion_record(
                source_sha256="hash-b",
                source_paths=("b.docx",),
                representative_path="b.docx",
                output_relative_path="b.docx.md",
            ),
        )
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        for name in ("a", "b"):
            path = store.current_path(f"{name}.docx.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {name}", encoding="utf-8")

        seen: list[tuple[int, int]] = []

        def guard(removals: int, previous_count: int) -> str | None:
            seen.append((removals, previous_count))
            return None

        result = self.run(
            tmp_path=tmp_path,
            groups=(source_group(("a.docx",), "hash-a"),),
            client=FakeDoclingClient({}),
            previous_manifest=previous,
            deletion_guard=guard,
        )

        assert seen == [(1, 2)]
        assert result.removed_paths == ("b.docx.md",)
        assert store.current_path("a.docx.md").is_file()
        assert not store.current_path("b.docx.md").exists()

    def test_mixed_results_commit_partial_manifest_and_valid_artifacts(
        self,
        tmp_path: Path,
    ) -> None:
        groups = (
            source_group(("a.docx",), "hash-a"),
            source_group(("b.pdf",), "hash-b"),
            source_group(("c.png",), "hash-c"),
        )
        partial_error = ingestion.ConversionError(
            component="page",
            code="page_failed",
            message="page 2 failed",
            page=2,
        )
        client = FakeDoclingClient(
            {
                "a.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown="# A",
                    task_id="task-a",
                ),
                "b.pdf": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.PARTIAL_SUCCESS,
                    markdown="# B partial",
                    task_id="task-b",
                    warnings=("some content omitted",),
                    errors=(partial_error,),
                    missing_pages=(2,),
                    missing_pages_known=True,
                ),
                "c.png": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.FAILURE,
                    task_id="task-c",
                    errors=(
                        ingestion.ConversionError(
                            component="ocr",
                            code="ocr_failed",
                            message="image could not be read",
                        ),
                    ),
                ),
            }
        )

        result = self.run(tmp_path=tmp_path, groups=groups, client=client)

        assert result.status is ingestion.IngestionRunStatus.PARTIAL_SUCCESS
        assert result.manifest.summary() == {
            "success": 1,
            "partial_success": 1,
            "failure": 1,
            "serving_previous_version": 0,
        }
        store = ingestion.ArtifactStore(tmp_path / "converted")
        assert store.current_path("a.docx.md").is_file()
        assert store.current_path("b.pdf.md").is_file()
        assert not store.current_path("c.png.md").exists()
        partial = next(
            record
            for record in result.manifest.documents
            if record.representative_path == "b.pdf"
        )
        assert partial.missing_pages == (2,)
        assert partial.errors == (partial_error,)
        partial_event = next(
            event
            for event in result.events
            if event.event == "conversion_partial_success"
        )
        assert partial_event.fields["warning_count"] == 1
        assert partial_event.fields["error_count"] == 1
        assert result.events[-1].event == "conversion_run_completed_with_errors"

    def test_source_issue_is_committed_and_marks_run_partial(
        self,
        tmp_path: Path,
    ) -> None:
        issue = ingestion.SourceIssue(
            relative_path="empty.pdf",
            code="source_validation_failed",
            message="zero-byte document",
        )

        result = self.run(
            tmp_path=tmp_path,
            groups=(),
            source_issues=(issue,),
            client=FakeDoclingClient({}),
        )

        assert result.status is ingestion.IngestionRunStatus.PARTIAL_SUCCESS
        assert result.manifest.source_issues == (issue,)
        assert any(event.event == "source_validation_failed" for event in result.events)
        loaded = ingestion.load_conversion_manifest(
            tmp_path / "converted" / "conversion-manifest.json"
        )
        assert loaded is not None
        assert loaded.source_issues == (issue,)

    def test_failed_update_retains_previous_then_converges_on_retry(
        self,
        tmp_path: Path,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        old_content = "# previous"
        old_path = store.current_path("manuals/device.docx.md")
        old_path.parent.mkdir(parents=True)
        old_path.write_text(old_content, encoding="utf-8")
        previous = conversion_record(
            output_sha256=ingestion.sha256_text(old_content),
            mrag_document_id="mrag-old",
        )
        previous_manifest = conversion_manifest(previous)
        updated = source_group(digest="new-source-hash")
        failed_client = FakeDoclingClient(
            {
                "manuals/device.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.FAILURE,
                    task_id="failed-task",
                )
            }
        )

        failed = self.run(
            tmp_path=tmp_path,
            groups=(updated,),
            client=failed_client,
            previous_manifest=previous_manifest,
        )

        retained = failed.manifest.documents[0]
        assert retained.serving_previous_version is True
        assert retained.served_source_sha256 == "source-hash"
        assert retained.mrag_document_id == "mrag-old"
        assert old_path.read_text(encoding="utf-8") == old_content

        successful_client = FakeDoclingClient(
            {
                "manuals/device.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown="# current",
                    task_id="success-task",
                )
            }
        )
        succeeded = self.run(
            tmp_path=tmp_path,
            groups=(updated,),
            client=successful_client,
            previous_manifest=failed.manifest,
            run_id="run-2",
        )

        assert succeeded.status is ingestion.IngestionRunStatus.SUCCESS
        assert succeeded.manifest.documents[0].serving_previous_version is False
        assert "# current" in old_path.read_text(encoding="utf-8")

    def test_same_generated_output_avoids_publish_and_preserves_mrag_id(
        self,
        tmp_path: Path,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        published = ingestion.build_published_markdown(
            markdown="# unchanged",
            source_path="manuals/device.docx",
            source_sha256="source-hash",
            status=ingestion.ConversionStatus.SUCCESS,
            converter_version="docling-serve test",
        )
        output_path = store.current_path("manuals/device.docx.md")
        output_path.parent.mkdir(parents=True)
        output_path.write_text(published, encoding="utf-8")
        previous = conversion_record(
            output_sha256=ingestion.sha256_text(published),
            mrag_document_id="mrag-existing",
        )
        client = FakeDoclingClient(
            {
                "manuals/device.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown="# unchanged",
                )
            }
        )

        result = self.run(
            tmp_path=tmp_path,
            groups=(source_group(),),
            client=client,
            previous_manifest=conversion_manifest(previous),
            fingerprint="sha256:new-fingerprint",
        )

        assert result.published_paths == ()
        assert result.unchanged_paths == ("manuals/device.docx.md",)
        assert result.manifest.documents[0].mrag_document_id == "mrag-existing"
        assert output_path.read_text(encoding="utf-8") == published

    def test_systemic_client_error_aborts_before_publish_or_manifest(
        self,
        tmp_path: Path,
    ) -> None:
        groups = (
            source_group(("a.docx",), "hash-a"),
            source_group(("b.docx",), "hash-b"),
        )
        client = FakeDoclingClient(
            {
                "a.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown="# staged only",
                ),
                "b.docx": ingestion.DoclingServiceError("service unavailable"),
            }
        )

        with pytest.raises(ingestion.DoclingServiceError, match="unavailable"):
            self.run(tmp_path=tmp_path, groups=groups, client=client)

        store = ingestion.ArtifactStore(tmp_path / "converted")
        assert list(store.current_root.rglob("*.md")) == []
        assert not (store.root / "conversion-manifest.json").exists()
        assert list(store.staging_root.rglob("*.md"))

    def test_deleted_source_is_pruned_after_manifest_commit(
        self,
        tmp_path: Path,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        kept_content = "# kept"
        removed_content = "# removed"
        for relative, content in (
            ("keep.docx.md", kept_content),
            ("remove.docx.md", removed_content),
        ):
            path = store.current_path(relative)
            path.write_text(content, encoding="utf-8")
        kept = conversion_record(
            source_paths=("keep.docx",),
            representative_path="keep.docx",
            output_relative_path="keep.docx.md",
            output_sha256=ingestion.sha256_text(kept_content),
        )
        removed = conversion_record(
            source_sha256="removed-hash",
            source_paths=("remove.docx",),
            representative_path="remove.docx",
            output_relative_path="remove.docx.md",
            output_sha256=ingestion.sha256_text(removed_content),
        )
        client = FakeDoclingClient({})

        result = self.run(
            tmp_path=tmp_path,
            groups=(source_group(("keep.docx",)),),
            client=client,
            previous_manifest=conversion_manifest(kept, removed),
        )

        assert result.removed_paths == ("remove.docx.md",)
        assert store.current_path("keep.docx.md").exists()
        assert not store.current_path("remove.docx.md").exists()
        assert client.calls == []

    def test_run_budget_defers_documents_and_keeps_serving_their_output(
        self,
        tmp_path: Path,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        stale = "# old b"
        store.current_path("b.docx.md").write_text(stale, encoding="utf-8")
        previous = conversion_manifest(
            conversion_record(
                source_sha256="old-hash-b",
                source_paths=("b.docx",),
                representative_path="b.docx",
                output_relative_path="b.docx.md",
                output_sha256=ingestion.sha256_text(stale),
            )
        )
        client = FakeDoclingClient(
            {
                "a.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown="# A",
                ),
                "b.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown="# new B",
                ),
            }
        )
        # Deadline base, the check that lets a.docx through, then a clock that
        # has run past the budget by the time b.docx would be submitted.
        readings = iter([0.0, 0.0])

        result = self.run(
            tmp_path=tmp_path,
            groups=(
                source_group(("a.docx",), "hash-a"),
                source_group(("b.docx",), "hash-b"),
            ),
            client=client,
            previous_manifest=previous,
            monotonic=lambda: next(readings, 999.0),
            DOCLING_RUN_TIMEOUT_SECONDS="10",
        )

        assert client.calls == ["a.docx"]
        assert result.deferred_sources == ("b.docx",)
        assert result.status is ingestion.IngestionRunStatus.PARTIAL_SUCCESS
        deferred = [
            event for event in result.events if event.event == "conversion_deferred"
        ]
        assert [event.fields["source_path"] for event in deferred] == ["b.docx"]
        assert deferred[0].fields["retained_output"] == "b.docx.md"
        # The previous record is carried forward verbatim, so the artifact stays
        # published and the next run re-derives exactly this conversion.
        assert store.current_path("b.docx.md").read_text(encoding="utf-8") == stale
        assert result.removed_paths == ()
        carried = next(
            record
            for record in result.manifest.documents
            if record.representative_path == "b.docx"
        )
        assert carried == previous.documents[0]

    def test_deferred_new_source_is_left_out_of_the_manifest(
        self,
        tmp_path: Path,
    ) -> None:
        client = FakeDoclingClient(
            {
                "a.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown="# A",
                )
            }
        )
        readings = iter([0.0, 0.0])

        result = self.run(
            tmp_path=tmp_path,
            groups=(
                source_group(("a.docx",), "hash-a"),
                source_group(("b.docx",), "hash-b"),
            ),
            client=client,
            monotonic=lambda: next(readings, 999.0),
            DOCLING_RUN_TIMEOUT_SECONDS="10",
        )

        assert client.calls == ["a.docx"]
        assert result.deferred_sources == ("b.docx",)
        # Nothing was ever converted for b.docx, so recording a failure would
        # invent an error the next run would have to explain away.
        assert [record.representative_path for record in result.manifest.documents] == [
            "a.docx"
        ]

    def test_concurrency_limit_bounds_conversions_in_flight(
        self,
        tmp_path: Path,
    ) -> None:
        lock = threading.Lock()
        state = {"active": 0, "peak": 0}

        class ConcurrentClient:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def convert(self, source: object, settings: object) -> object:
                with lock:
                    self.calls.append(source.relative_path)
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                time.sleep(0.05)
                with lock:
                    state["active"] -= 1
                return ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown=f"# {source.relative_path}",
                )

        client = ConcurrentClient()
        names = ("a", "b", "c", "d")

        result = self.run(
            tmp_path=tmp_path,
            groups=tuple(
                source_group((f"{name}.docx",), f"hash-{name}") for name in names
            ),
            client=client,
            DOCLING_BATCH_CONCURRENCY="2",
        )

        assert state["peak"] == 2
        assert sorted(client.calls) == [f"{name}.docx" for name in names]
        # Submission overlaps, but every published path and event still follows
        # the plan, so the manifest does not depend on which worker finished first.
        assert result.published_paths == tuple(f"{name}.docx.md" for name in names)
        completed = [
            event.fields["source_path"]
            for event in result.events
            if event.event == "conversion_completed"
        ]
        assert completed == [f"{name}.docx" for name in names]

    def test_orphan_artifacts_are_swept_but_convertible_sources_are_kept(
        self,
        tmp_path: Path,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        # Published by a run that died before writing the manifest; no source
        # produces it any more and no manifest mentions it.
        orphan = store.current_path("gone/old.docx.md")
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_text("# orphan", encoding="utf-8")
        retained = "# kept despite failure"
        store.current_path("b.docx.md").write_text(retained, encoding="utf-8")
        client = FakeDoclingClient(
            {
                "a.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.SUCCESS,
                    markdown="# A",
                ),
                "b.docx": ingestion.ConversionAttempt(
                    status=ingestion.ConversionStatus.FAILURE,
                ),
            }
        )

        result = self.run(
            tmp_path=tmp_path,
            groups=(
                source_group(("a.docx",), "hash-a"),
                source_group(("b.docx",), "hash-b"),
            ),
            client=client,
        )

        assert result.plan.orphan_paths == ("gone/old.docx.md",)
        assert result.removed_paths == ("gone/old.docx.md",)
        assert not store.current_path("gone/old.docx.md").exists()
        # The emptied directory goes with it.
        assert not (store.current_root / "gone").exists()
        # b.docx failed to convert with no manifest entry to fall back on, but a
        # live source could still produce that path, so its Markdown survives.
        assert store.current_path("b.docx.md").read_text(encoding="utf-8") == retained
        removals = [
            (event.fields["output_relative_path"], event.fields["reason"])
            for event in result.events
            if event.event == "conversion_removed"
        ]
        assert removals == [("gone/old.docx.md", "orphan_artifact")]

    def test_orphan_sweep_is_counted_by_the_deletion_guard(
        self,
        tmp_path: Path,
    ) -> None:
        store = ingestion.ArtifactStore(tmp_path / "converted")
        store.prepare()
        for index in range(4):
            store.current_path(f"gone-{index}.docx.md").write_text(
                "# orphan",
                encoding="utf-8",
            )
        seen: list[tuple[int, int]] = []

        def guard(removals: int, previous_count: int) -> str | None:
            seen.append((removals, previous_count))
            return "too many"

        client = FakeDoclingClient({})
        with pytest.raises(ingestion.LargeDeletionError, match="deletion guard"):
            self.run(
                tmp_path=tmp_path,
                groups=(),
                client=client,
                deletion_guard=guard,
            )

        # A lost manifest reports no previous documents; the artifacts on disk
        # keep the percentage rule meaningful.
        assert seen == [(4, 4)]
        assert len(list(store.current_root.glob("*.md"))) == 4


class TestDoclingAdmin:
    def run_admin(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "batch" / "docling_admin.py"),
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_plan_reports_actions_and_source_issues(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        converted = tmp_path / "converted"
        source.mkdir()
        (source / "new.docx").write_bytes(b"new")
        (source / "empty.pdf").touch()

        result = self.run_admin(
            "plan",
            "--source-dir",
            str(source),
            "--converted-dir",
            str(converted),
            "--fingerprint",
            "sha256:test",
        )

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["mode"] == "dry-run"
        assert payload["summary"]["convert"] == 1
        assert payload["summary"]["source_issues"] == 1

    def test_status_lists_partial_failure_and_previous_version(
        self,
        tmp_path: Path,
    ) -> None:
        converted = tmp_path / "converted"
        manifest = conversion_manifest(
            conversion_record(
                status=ingestion.ConversionStatus.PARTIAL_SUCCESS,
                missing_pages=(2,),
                missing_pages_known=True,
            ),
            conversion_record(
                source_sha256="failed-hash",
                source_paths=("failed.docx",),
                representative_path="failed.docx",
                status=ingestion.ConversionStatus.FAILURE,
                output_relative_path="old.docx.md",
                serving_previous_version=True,
                served_source_sha256="old-hash",
            ),
        )
        ingestion.write_conversion_manifest(
            converted / "conversion-manifest.json",
            manifest,
        )

        result = self.run_admin(
            "status",
            "--converted-dir",
            str(converted),
        )

        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["summary"]["partial_success"] == 1
        assert payload["summary"]["failure"] == 1
        assert len(payload["unresolved"]) == 2

    def test_status_reports_corrupt_manifest_without_modifying_it(
        self,
        tmp_path: Path,
    ) -> None:
        converted = tmp_path / "converted"
        converted.mkdir()
        manifest = converted / "conversion-manifest.json"
        manifest.write_text("{broken", encoding="utf-8")

        result = self.run_admin(
            "status",
            "--converted-dir",
            str(converted),
        )

        assert result.returncode == 1
        assert json.loads(result.stdout)["status"] == "error"
        assert manifest.read_text(encoding="utf-8") == "{broken"

    def test_cleanup_staging_is_dry_run_until_explicitly_executed(
        self,
        tmp_path: Path,
    ) -> None:
        converted = tmp_path / "converted"
        stale = converted / "staging" / "stale-run"
        stale.mkdir(parents=True)
        (stale / "partial.md").write_text("partial", encoding="utf-8")
        old = time.time() - 3600
        os.utime(stale, (old, old))
        arguments = (
            "cleanup-staging",
            "--converted-dir",
            str(converted),
            "--older-than-seconds",
            "60",
        )

        dry_run = self.run_admin(*arguments)

        assert dry_run.returncode == 0
        assert json.loads(dry_run.stdout)["candidates"] == ["stale-run"]
        assert stale.exists()

        executed = self.run_admin(*arguments, "--execute")

        assert executed.returncode == 0
        assert json.loads(executed.stdout)["removed"] == ["stale-run"]
        assert not stale.exists()


def test_generate_env_replaces_docling_api_key(tmp_path: Path) -> None:
    template = tmp_path / ".env.example"
    destination = tmp_path / ".env"
    template.write_text(
        "DOCLING_SERVE_API_KEY=CHANGE_ME\n",
        encoding="utf-8",
    )

    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate-env.py"),
            str(template),
            str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    generated = destination.read_text(encoding="utf-8").strip()
    assert generated.startswith("DOCLING_SERVE_API_KEY=")
    assert generated != "DOCLING_SERVE_API_KEY=CHANGE_ME"


def test_windows_init_uses_shared_environment_generator() -> None:
    script = (ROOT / "win-scripts" / "init-env.ps1").read_text(encoding="utf-8")

    assert "generate-env.py" in script
    assert ".env.example" in script
