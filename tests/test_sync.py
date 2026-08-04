from __future__ import annotations

import fcntl
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mrag_sync", ROOT / "batch" / "sync.py")
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)


def create_database(project: Path, documents: list[tuple[str, str, str]]) -> None:
    connection = sqlite3.connect(project / "mrag.db")
    connection.execute(
        "CREATE TABLE documents (id TEXT, filename TEXT, file_hash TEXT)"
    )
    connection.executemany(
        "INSERT INTO documents (id, filename, file_hash) VALUES (?, ?, ?)",
        documents,
    )
    connection.commit()
    connection.close()


class ExclusiveLockTests(unittest.TestCase):
    """The lock must already be held when the conversion phase starts.

    The conversion phase runs before mrag and mutates the converted tree, so a
    lock taken only around the mrag phase would let two runs convert at once.
    """

    def test_conversion_phase_runs_while_the_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            source = root / "source"
            project.mkdir()
            source.mkdir()
            lock_path = project / ".sync.lock"
            observed: dict[str, object] = {}

            def fake_docling_phase(**kwargs: object) -> None:
                # Try to take the same lock from an independent descriptor: it
                # must fail, proving the caller already holds it.
                with lock_path.open("w", encoding="utf-8") as probe:
                    try:
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        observed["locked_during_conversion"] = True
                    else:
                        observed["locked_during_conversion"] = False
                        fcntl.flock(probe, fcntl.LOCK_UN)
                observed["force"] = kwargs.get("force")

            original_phase = sync.run_docling_phase
            original_emit = sync.emit
            events: list[str] = []
            sync.run_docling_phase = fake_docling_phase
            sync.emit = lambda event, **fields: events.append(event)
            try:
                exit_code = sync.main(
                    [
                        "--source-dir", str(source),
                        "--project-dir", str(project),
                        "--converted-root", str(root / "converted"),
                        "--backup-dir", str(root / "backup"),
                        "--convert-only",
                        "--force-reconvert",
                    ]
                )
            finally:
                sync.run_docling_phase = original_phase
                sync.emit = original_emit

            self.assertEqual(0, exit_code)
            self.assertTrue(observed["locked_during_conversion"])
            self.assertTrue(observed["force"])
            # --convert-only stops before mrag runs.
            self.assertIn("convert_only_completed", events)

    def test_second_run_is_skipped_while_the_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            source = root / "source"
            project.mkdir()
            source.mkdir()

            holder = (project / ".sync.lock").open("w", encoding="utf-8")
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            called: list[str] = []
            original_phase = sync.run_docling_phase
            original_emit = sync.emit
            events: list[str] = []
            sync.run_docling_phase = lambda **kwargs: called.append("ran")
            sync.emit = lambda event, **fields: events.append(event)
            try:
                exit_code = sync.main(
                    [
                        "--source-dir", str(source),
                        "--project-dir", str(project),
                        "--converted-root", str(root / "converted"),
                        "--backup-dir", str(root / "backup"),
                        "--convert-only",
                    ]
                )
            finally:
                sync.run_docling_phase = original_phase
                sync.emit = original_emit
                fcntl.flock(holder, fcntl.LOCK_UN)
                holder.close()

            self.assertEqual(0, exit_code)
            self.assertEqual([], called)
            self.assertEqual(["sync_skipped"], events)


class IndexHealthTests(unittest.TestCase):
    def _database(self, project: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(project / "mrag.db")
        connection.executescript(
            """
            CREATE TABLE documents (
              id TEXT, filename TEXT, file_hash TEXT,
              status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE document_indexes (document_id TEXT, status TEXT);
            CREATE TABLE document_exclusions (document_id TEXT, revoked_at TEXT);
            CREATE TABLE chunk_variants (document_id TEXT, metadata_json TEXT);
            """
        )
        return connection

    def test_counts_every_error_and_omission_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            connection = self._database(project)
            connection.executescript(
                """
                INSERT INTO documents VALUES ('d1', 'a.md', 'h1', 'extracted');
                INSERT INTO documents VALUES ('d2', 'b.md', 'h2', 'error');
                INSERT INTO document_indexes VALUES ('d2', 'error');
                INSERT INTO document_indexes VALUES ('d3', 'pending');
                INSERT INTO document_exclusions VALUES ('d4', NULL);
                INSERT INTO document_exclusions VALUES ('d5', '2026-08-01');
                """
            )
            # Two variants of one document degraded; it must count once.
            for _ in range(2):
                connection.execute(
                    "INSERT INTO chunk_variants VALUES ('d1', ?)",
                    (json.dumps({"embedding_status": "fallback_no_vector"}),),
                )
            connection.execute(
                "INSERT INTO chunk_variants VALUES ('d2', ?)",
                (json.dumps({"augmentation_status": "fallback_raw"}),),
            )
            connection.execute("INSERT INTO chunk_variants VALUES ('d3', NULL)")
            connection.commit()
            connection.close()

            health = sync.load_index_health(project)

            self.assertEqual(
                {
                    "documents_error": 1,
                    "index_error": 1,
                    "index_pending": 1,
                    "documents_excluded": 1,
                    "documents_without_vectors": 1,
                    "documents_raw_fallback": 1,
                },
                health,
            )

    def test_healthy_index_reports_all_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            connection = self._database(project)
            connection.execute(
                "INSERT INTO documents VALUES ('d1', 'a.md', 'h1', 'extracted')"
            )
            connection.execute("INSERT INTO document_indexes VALUES ('d1', 'indexed')")
            connection.commit()
            connection.close()

            self.assertEqual(0, sum(sync.load_index_health(project).values()))

    def test_older_schema_without_the_tables_is_not_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            create_database(project, [("d1", "a.md", "h1")])

            self.assertEqual(0, sum(sync.load_index_health(project).values()))

    def test_missing_database_is_reported(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(RuntimeError, "does not exist"),
        ):
            sync.load_index_health(Path(directory))


class ScanTests(unittest.TestCase):
    def test_only_supported_non_hidden_regular_files_are_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("alpha", encoding="utf-8")
            (root / "B.MARKDOWN").write_text("bravo", encoding="utf-8")
            # mrag 1.0.0 rejects both of these, so the converted tree must never
            # hand them to `mrag add`.
            (root / "ignored.pdf").write_bytes(b"pdf")
            (root / "ignored.docx").write_text("x", encoding="utf-8")
            (root / ".hidden.txt").write_text("hidden", encoding="utf-8")

            result = sync.scan_source(root)

            self.assertEqual(
                ["B.MARKDOWN", "a.md"], [item.relative_path for item in result]
            )

    def test_zero_byte_document_aborts_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty.txt").touch()
            with self.assertRaisesRegex(RuntimeError, "zero-byte"):
                sync.scan_source(root)


class PlanTests(unittest.TestCase):
    def source(self, path: str, digest: str) -> object:
        return sync.SourceDocument(path, Path("/") / path, digest, 1, 1)

    def test_plan_adds_and_removes_by_content_hash(self) -> None:
        sources = [self.source("new.md", "new"), self.source("same.txt", "same")]
        indexed = [
            sync.IndexedDocument("old-id", "old.md", "old"),
            sync.IndexedDocument("same-id", "same.txt", "same"),
        ]

        plan = sync.build_plan(sources, indexed)

        self.assertEqual(("new",), plan.add_hashes)
        self.assertEqual(("old-id",), plan.remove_ids)

    def test_duplicate_source_content_is_one_indexed_document(self) -> None:
        sources = [self.source("a.md", "same"), self.source("b.md", "same")]
        plan = sync.build_plan(sources, [])
        self.assertEqual(("same",), plan.add_hashes)
        self.assertEqual(("same",), plan.desired_hashes)

    def test_same_path_with_changed_content_is_add_and_remove(self) -> None:
        sources = [self.source("manual.md", "new")]
        indexed = [sync.IndexedDocument("old-id", "manual.md", "old")]
        plan = sync.build_plan(sources, indexed)
        self.assertEqual(("new",), plan.add_hashes)
        self.assertEqual(("old-id",), plan.remove_ids)

    def test_deletion_guard_checks_percentage_and_count(self) -> None:
        self.assertIsNotNone(sync.deletion_guard_reason(2, 10, 20, 50))
        self.assertIsNotNone(sync.deletion_guard_reason(50, 1000, 20, 50))
        self.assertIsNone(sync.deletion_guard_reason(1, 10, 20, 50))


class IntegrationTests(unittest.TestCase):
    def test_sync_adds_indexes_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            project = root / "project"
            backup = root / "backup"
            source.mkdir()
            project.mkdir()
            file = source / "guide.md"
            file.write_text("# Guide\nhello", encoding="utf-8")
            digest = sync.sha256_file(file)[0]
            create_database(project, [])
            commands: list[list[str]] = []

            def runner(command: object, cwd: Path) -> subprocess.CompletedProcess[str]:
                command = list(command)
                commands.append(command)
                if command[:2] == ["mrag", "add"]:
                    connection = sqlite3.connect(project / "mrag.db")
                    connection.execute(
                        "INSERT INTO documents VALUES (?, ?, ?)",
                        ("doc-1", "guide.md", digest),
                    )
                    connection.commit()
                    connection.close()
                    output = {
                        "items": [{"document_id": "doc-1", "status": "added"}]
                    }
                    return subprocess.CompletedProcess(command, 0, json.dumps(output), "")
                return subprocess.CompletedProcess(command, 0, "", "")

            manifest = project / "sync-manifest.json"
            sync.run_sync(
                source_dir=source,
                project_dir=project,
                backup_dir=backup,
                manifest_path=manifest,
                max_delete_percent=20,
                max_delete_count=50,
                backup_retention=7,
                dry_run=False,
                allow_large_delete=False,
                runner=runner,
            )

            self.assertEqual("mrag", commands[0][0])
            self.assertEqual(["mrag", "index"], commands[-1])
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual("doc-1", payload["documents"][0]["document_id"])
            self.assertEqual(1, len(list(backup.glob("*.tar.gz"))))

            commands.clear()
            sync.run_sync(
                source_dir=source,
                project_dir=project,
                backup_dir=backup,
                manifest_path=manifest,
                max_delete_percent=20,
                max_delete_count=50,
                backup_retention=7,
                dry_run=False,
                allow_large_delete=False,
                runner=runner,
            )
            self.assertEqual([], commands)

    def test_degraded_index_reports_completion_with_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            project = root / "project"
            backup = root / "backup"
            source.mkdir()
            project.mkdir()
            file = source / "guide.md"
            file.write_text("# Guide\nhello", encoding="utf-8")
            digest = sync.sha256_file(file)[0]
            create_database(project, [("doc-1", "guide.md", digest)])
            connection = sqlite3.connect(project / "mrag.db")
            connection.execute(
                "CREATE TABLE chunk_variants (document_id TEXT, metadata_json TEXT)"
            )
            # The document is present and its hash matches, so post-index
            # validation passes, yet the chunk carries no vector.
            connection.execute(
                "INSERT INTO chunk_variants VALUES ('doc-1', ?)",
                (json.dumps({"embedding_status": "fallback_no_vector"}),),
            )
            connection.commit()
            connection.close()

            events: list[tuple[str, dict]] = []
            original_emit = sync.emit
            sync.emit = lambda event, **fields: events.append((event, fields))
            try:
                sync.run_sync(
                    source_dir=source,
                    project_dir=project,
                    backup_dir=backup,
                    manifest_path=project / "sync-manifest.json",
                    max_delete_percent=20,
                    max_delete_count=50,
                    backup_retention=7,
                    dry_run=False,
                    allow_large_delete=False,
                    conversion_summary={
                        "success": 3,
                        "partial_success": 1,
                        "failure": 2,
                        "serving_previous_version": 1,
                    },
                    runner=lambda command, cwd: subprocess.CompletedProcess(
                        list(command), 0, "", ""
                    ),
                )
            finally:
                sync.emit = original_emit

            names = [name for name, _ in events]
            self.assertIn("index_health_degraded", names)
            self.assertIn("sync_completed_with_errors", names)
            self.assertNotIn("sync_completed", names)
            completion = dict(events[-1][1])
            self.assertEqual(1, completion["documents_without_vectors"])
            # Conversion and mrag counts arrive as one combined tally.
            self.assertEqual(3, completion["converted_success"])
            self.assertEqual(1, completion["converted_partial_success"])
            self.assertEqual(2, completion["converted_failure"])
            self.assertEqual(0, completion["index_error"])

    def test_failed_index_is_retried_when_manifest_was_not_committed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            project = root / "project"
            backup = root / "backup"
            source.mkdir()
            project.mkdir()
            file = source / "guide.txt"
            file.write_text("important procedure", encoding="utf-8")
            digest = sync.sha256_file(file)[0]
            create_database(project, [])

            def failing_runner(
                command: object, cwd: Path
            ) -> subprocess.CompletedProcess[str]:
                command = list(command)
                if command[:2] == ["mrag", "add"]:
                    connection = sqlite3.connect(project / "mrag.db")
                    connection.execute(
                        "INSERT INTO documents VALUES (?, ?, ?)",
                        ("doc-1", "guide.txt", digest),
                    )
                    connection.commit()
                    connection.close()
                    return subprocess.CompletedProcess(
                        command,
                        0,
                        json.dumps({"items": [{"document_id": "doc-1"}]}),
                        "",
                    )
                raise subprocess.CalledProcessError(1, command, "", "index failed")

            manifest = project / "sync-manifest.json"
            with self.assertRaises(subprocess.CalledProcessError):
                sync.run_sync(
                    source_dir=source,
                    project_dir=project,
                    backup_dir=backup,
                    manifest_path=manifest,
                    max_delete_percent=20,
                    max_delete_count=50,
                    backup_retention=7,
                    dry_run=False,
                    allow_large_delete=False,
                    runner=failing_runner,
                )
            self.assertFalse(manifest.exists())

            commands: list[list[str]] = []

            def successful_runner(
                command: object, cwd: Path
            ) -> subprocess.CompletedProcess[str]:
                commands.append(list(command))
                return subprocess.CompletedProcess(command, 0, "", "")

            sync.run_sync(
                source_dir=source,
                project_dir=project,
                backup_dir=backup,
                manifest_path=manifest,
                max_delete_percent=20,
                max_delete_count=50,
                backup_retention=7,
                dry_run=False,
                allow_large_delete=False,
                runner=successful_runner,
            )
            self.assertEqual([["mrag", "index"]], commands)
            self.assertTrue(manifest.exists())


if __name__ == "__main__":
    unittest.main()
