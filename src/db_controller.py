# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import json
import logging
import sqlite3
from collections.abc import Sequence
from sqlmodel import SQLModel, Session, create_engine, select, col
from sqlalchemy import Connection, Table, event, text
from pathlib import Path

from db_schema import (DatabaseFileRegistry, DatabaseLLMAnswer,
                       DatabaseLLMRequest, DatabasePageLog,
                       DatabaseRunConfiguration)
from fs_utils import format_hms, get_safe_path, replace_lone_surrogates
from range_parsers import format_page_list
from schemas import (ConfigurationError, RequestOutcome, RunConfiguration,
                     PageResult, FileSummary, SourceFingerprint, Status)

database_logger = logging.getLogger("DatabaseController")

_VIEW_STATEMENTS = (
    "DROP VIEW IF EXISTS file_to_jpegs_status",
    """
    CREATE VIEW file_to_jpegs_status AS
    SELECT f.file_id,
           CASE
               WHEN f.range_status = 'skipped' THEN 'skipped'
               WHEN COALESCE(p.usable, 0) = 0 THEN 'processing_failure'
               WHEN COALESCE(p.failed, 0) > 0
                    OR f.range_status IN ('failure', '')
                   THEN 'partial_processing_failure'
               ELSE 'ok'
           END AS file_to_jpegs_status
    FROM file_registry f
    LEFT JOIN (
        SELECT file_id,
               SUM(CASE WHEN page_to_jpeg_status IN ('ok', 'skipped')
                        THEN 1 ELSE 0 END) AS usable,
               SUM(CASE WHEN page_to_jpeg_status NOT IN ('ok', 'skipped')
                        THEN 1 ELSE 0 END) AS failed
        FROM page_log GROUP BY file_id
    ) p ON p.file_id = f.file_id
    """,
    "DROP VIEW IF EXISTS file_to_llm_status",
    """
    CREATE VIEW file_to_llm_status AS
    SELECT f.file_id,
           CASE
               WHEN f.ai_enabled AND NOT f.processing_complete
                   THEN CASE WHEN c.ok > 0 THEN 'partial_llm_failure' ELSE 'llm_failure' END
               WHEN c.total IS NULL THEN 'skipped'
               WHEN c.ok = c.total AND COALESCE(a.errors, 0) = 0 THEN 'ok'
               WHEN c.ok > 0 THEN 'partial_llm_failure'
               ELSE 'llm_failure'
           END AS file_to_llm_status
    FROM file_registry f
    LEFT JOIN (
        SELECT file_id, COUNT(*) AS total,
               SUM(CASE WHEN request_status = 'ok' THEN 1 ELSE 0 END) AS ok
        FROM llm_requests GROUP BY file_id
    ) c ON c.file_id = f.file_id
    LEFT JOIN (
        SELECT file_id, SUM(CASE WHEN llm_error != '' THEN 1 ELSE 0 END) AS errors
        FROM llm_answers GROUP BY file_id
    ) a ON a.file_id = f.file_id
    """,
    "DROP VIEW IF EXISTS overall_result",
    """
    CREATE VIEW overall_result AS
    SELECT j.file_id,
           CASE
               WHEN NOT f.processing_complete THEN
                   CASE WHEN j.file_to_jpegs_status IN ('ok', 'partial_processing_failure')
                        THEN 'partial_fail' ELSE 'fail' END
               WHEN j.file_to_jpegs_status = 'skipped' THEN 'skipped'
               WHEN j.file_to_jpegs_status = 'processing_failure' THEN 'fail'
               WHEN j.file_to_jpegs_status = 'ok'
                    AND l.file_to_llm_status IN ('ok', 'skipped') THEN 'ok'
               ELSE 'partial_fail'
           END AS overall_result
    FROM file_to_jpegs_status j
    JOIN file_registry f USING(file_id)
    JOIN file_to_llm_status l ON l.file_id = j.file_id
    """,
)


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _schema_state(connection: Connection) -> tuple[bool, bool]:
    tables = set(connection.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'")).scalars())
    with_rows = {name for name in tables if connection.execute(
        text(f"SELECT 1 FROM {_quoted(name)} LIMIT 1")).first() is not None}
    declared = SQLModel.metadata.tables
    compatible = not with_rows - declared.keys() and all(
        name in tables and _table_matches(connection, table) for name, table in declared.items())
    return bool(with_rows - {"run_configuration"}), compatible


def _table_matches(connection: Connection, table: Table) -> bool:
    name = _quoted(table.name)
    columns = {row[1] for row in connection.execute(text(f"PRAGMA table_info({name})"))}
    if columns != {column.name for column in table.columns}:
        return False
    unique_by_name = {row[1]: bool(row[2]) for row in connection.execute(text(f"PRAGMA index_list({name})"))}
    return all(
        unique_by_name.get(str(index.name)) == bool(index.unique)
        and [row[2] for row in connection.execute(text(f"PRAGMA index_info({_quoted(str(index.name))})"))]
        == [column.name for column in index.columns]
        for index in table.indexes)


class SQLiteDatabaseController:

    def __init__(self, target_database_path: Path):
        self.database_path = target_database_path
        safe_database_path = get_safe_path(self.database_path)
        self.sql_engine = create_engine(
            "sqlite://",
            creator=lambda: sqlite3.connect(safe_database_path, check_same_thread=False),
            echo=False,
        )

        @event.listens_for(self.sql_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        with self.sql_engine.connect() as connection:
            populated, compatible = _schema_state(connection)
        if populated and not compatible:
            self.sql_engine.dispose()
            raise ConfigurationError(
                f"i18n:err_resume_old_database|{self.database_path}",
                setting_field="START_OVER", path=str(self.database_path))
        if not compatible:
            self._rebuild_empty_database()
        with self.sql_engine.begin() as connection:
            for statement in _VIEW_STATEMENTS:
                connection.execute(text(statement))
        database_logger.info(f"SQLite database initialized successfully at: {self.database_path}")

    def _rebuild_empty_database(self) -> None:
        run_configuration = SQLModel.metadata.tables["run_configuration"]
        with self.sql_engine.begin() as connection:
            objects = connection.execute(text(
                "SELECT type, name FROM sqlite_master WHERE type IN ('view', 'table') "
                "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY type DESC")).all()
            kept = None
            if ("table", "run_configuration") in objects:
                kept = next((dict(row) for row in connection.execute(text(
                    "SELECT * FROM run_configuration")).mappings()
                    if row.get("configuration_id") == 1), None)
            for kind, name in objects:
                connection.execute(text(f"DROP {kind.upper()} {_quoted(name)}"))
            SQLModel.metadata.create_all(connection)
            required = {column.name for column in run_configuration.columns
                        if not column.nullable and column.default is None}
            if kept is not None and required <= kept.keys():
                connection.execute(run_configuration.insert().values(
                    {column.name: kept[column.name] for column in run_configuration.columns
                     if column.name in kept}))

    def record_run_configuration(self, ai_enabled: bool, output_mode: str,
                                 declared_columns: Sequence[str]) -> None:
        if not ai_enabled and (output_mode or declared_columns):
            raise ValueError(
                "with AI off the run configuration carries no output mode and no "
                f"columns, got {output_mode!r}, {tuple(declared_columns)!r}")
        with Session(self.sql_engine) as active_session:
            existing = active_session.get(DatabaseRunConfiguration, 1)
            source_root = existing.source_root if existing else ""
            digest = existing.processing_digest if existing else ""
            for stale in active_session.exec(select(DatabaseRunConfiguration)).all():
                active_session.delete(stale)
            active_session.flush()
            active_session.add(DatabaseRunConfiguration(
                configuration_id=1,
                source_root=source_root, processing_digest=digest,
                ai_enabled=bool(ai_enabled),
                output_mode=output_mode,
                declared_columns=json.dumps(list(declared_columns)),
            ))
            active_session.commit()

    def get_run_configuration(self) -> RunConfiguration | None:
        with Session(self.sql_engine) as active_session:
            row = active_session.get(DatabaseRunConfiguration, 1)
            if row is None:
                return None
            return RunConfiguration(bool(row.ai_enabled), row.output_mode,
                                    tuple(json.loads(row.declared_columns)))

    def get_highest_file_id(self) -> int:
        with Session(self.sql_engine) as active_session:
            statement = select(DatabaseFileRegistry.file_id).order_by(
                col(DatabaseFileRegistry.file_id).desc())
            highest_id = active_session.exec(statement).first()
            return highest_id if highest_id is not None else 0

    def handle_file_started(self, file_id: int, file_path: str,
                            detected_extension: str, pipeline_name: str, *,
                            ai_enabled: bool = False,
                            source: SourceFingerprint | None = None) -> None:
        with Session(self.sql_engine) as active_session:
            new_registry_entry = DatabaseFileRegistry(
                file_id=file_id, ai_enabled=ai_enabled,
                source_size=source.size if source else None,
                source_mtime_ns=source.mtime_ns if source else None,
                source_sha256=source.sha256 if source else "",
                file_path=replace_lone_surrogates(file_path),
                file_ext=detected_extension,
                total_pages=0,
                file_to_jpegs_comment=f"Assigned to: {pipeline_name}"
            )
            active_session.add(new_registry_entry)
            active_session.commit()

    def handle_frame_saved(self, file_id: int, page_result: PageResult) -> None:
        with Session(self.sql_engine) as active_session:
            new_page_log = DatabasePageLog(
                file_id=file_id,
                page_number=page_result.page_number,
                output_file=replace_lone_surrogates(page_result.output_filename),
                page_to_jpeg_status=page_result.success,
                page_to_jpeg_comment=replace_lone_surrogates(page_result.comment),
                video_frame_timestamp=(
                    format_hms(page_result.capture_seconds)
                    if page_result.capture_seconds is not None else ""
                )
            )
            active_session.add(new_page_log)
            active_session.commit()

    def handle_file_completed(self, file_id: int, file_summary: FileSummary) -> None:
        assert file_summary.range_status, (
            "completion requires a non-empty range_status")
        with Session(self.sql_engine) as active_session:
            existing_record = active_session.get(DatabaseFileRegistry, file_id)
            if existing_record:
                existing_record.total_pages = file_summary.total_pages
                existing_record.file_to_jpegs_comment = replace_lone_surrogates(
                    file_summary.file_to_jpegs_comment)
                existing_record.page_range = replace_lone_surrogates(file_summary.page_range)
                existing_record.range_status = file_summary.range_status

                active_session.add(existing_record)
                active_session.commit()
            else:
                database_logger.warning(
                    f"handle_file_completed: no registry record found for "
                    f"file ID {file_id}; completion not recorded."
                )

    def handle_llm_requests(self, file_id: int, outcomes: Sequence[RequestOutcome]) -> None:
        if not outcomes:
            return
        spelled_pages = [format_page_list(outcome.pages) for outcome in outcomes]
        with Session(self.sql_engine) as active_session:
            page_id_by_number: dict[int, int | None] = {}
            if any(outcome.answer_rows for outcome in outcomes):
                page_rows = active_session.exec(
                    select(DatabasePageLog.page_number, DatabasePageLog.page_id)
                    .where(DatabasePageLog.file_id == file_id,
                           col(DatabasePageLog.page_number).in_(
                               {a.page_number for o in outcomes for a in o.answer_rows
                                if a.page_number is not None}))).all()
                page_id_by_number = dict(page_rows)
            for outcome, pages in zip(outcomes, spelled_pages, strict=True):
                request_row = DatabaseLLMRequest(
                    file_id=file_id,
                    request_number=outcome.request_number,
                    pages=pages,
                    request_status=outcome.status,
                    raw_llm_answer=replace_lone_surrogates(outcome.raw_answer),
                    llm_network_error=replace_lone_surrogates(outcome.error),
                )
                active_session.add(request_row)
                if outcome.answer_rows:
                    active_session.flush()
                    assert request_row.request_id is not None
                    for answer in outcome.answer_rows:
                        active_session.add(DatabaseLLMAnswer(
                            request_id=request_row.request_id,
                            file_id=file_id,
                            page_id=(page_id_by_number.get(answer.page_number)
                                     if answer.page_number is not None else None),
                            raw_model_page_number=replace_lone_surrogates(
                                answer.raw_model_page_number),
                            llm_error=answer.llm_error,
                            values_json=replace_lone_surrogates(answer.values_json),
                        ))
            active_session.commit()

    def get_file_statuses(self, file_id: int) -> dict[str, str]:
        with self.sql_engine.connect() as connection:
            row = connection.execute(text(
                "SELECT j.file_to_jpegs_status, l.file_to_llm_status, "
                "o.overall_result "
                "FROM file_to_jpegs_status j "
                "JOIN file_to_llm_status l ON l.file_id = j.file_id "
                "JOIN overall_result o ON o.file_id = j.file_id "
                "WHERE j.file_id = :file_id"), {"file_id": file_id}).first()
        if row is None:
            return {}
        return {
            "file_to_jpegs_status": row[0],
            "file_to_llm_status": row[1],
            "overall_result": row[2],
        }

    def get_no_retry_sources(self, no_retry_results: list[str]) -> dict[str, list[SourceFingerprint]]:
        if not no_retry_results:
            return {}
        placeholders = ", ".join(f":v{i}" for i in range(len(no_retry_results)))
        parameters = {f"v{i}": value for i, value in enumerate(no_retry_results)}
        with self.sql_engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT f.file_path, f.source_size, f.source_mtime_ns, f.source_sha256 "
                "FROM file_registry f JOIN overall_result o ON o.file_id = f.file_id "
                f"WHERE o.overall_result IN ({placeholders}) ORDER BY f.file_id"), parameters).all()
        sources: dict[str, list[SourceFingerprint]] = {}
        for path, size, mtime_ns, sha256 in rows:
            fingerprints = sources.setdefault(path, [])
            if size is not None and mtime_ns is not None:
                fingerprints.append(SourceFingerprint(size, mtime_ns, sha256))
        return sources

    def get_successful_frames(self, target_file_id: int,
                              base_output_folder: Path) -> list[tuple[int, Path, str]]:
        with Session(self.sql_engine) as active_session:
            statement = select(
                DatabasePageLog.page_number, DatabasePageLog.output_file,
                DatabasePageLog.video_frame_timestamp
            ).where(
                DatabasePageLog.file_id == target_file_id,
                DatabasePageLog.page_to_jpeg_status == Status.OK.value
            ).order_by(col(DatabasePageLog.page_number))
            rows = active_session.exec(statement).all()
            return [(page_number, base_output_folder / output_file, timestamp)
                    for page_number, output_file, timestamp in rows]

    def ensure_run_identity(self, source_root: str, digest: str) -> None:
        with Session(self.sql_engine) as session:
            row = session.get(DatabaseRunConfiguration, 1)
            if row is None:
                raise ValueError("Record run configuration before its processing identity")
            if row.source_root or row.processing_digest:
                if (row.source_root, row.processing_digest) != (source_root, digest):
                    raise ConfigurationError(
                        "i18n:err_resume_ai_work_changed|The source folder or processing settings changed.",
                        setting_field="START_OVER", detail_key="err_resume_ai_context_changed_detail")
                return
            if session.exec(select(DatabaseFileRegistry.file_id).limit(1)).first() is not None:
                raise ConfigurationError(f"i18n:err_resume_old_database|{self.database_path}",
                                         setting_field="START_OVER", path=str(self.database_path))
            row.source_root, row.processing_digest = source_root, digest
            session.add(row)
            session.commit()

    def finalize_file(self, file_id: int, expected_requests: int = 0) -> None:
        with Session(self.sql_engine) as session:
            row = session.get(DatabaseFileRegistry, file_id)
            if row is None or not row.range_status:
                raise ValueError("Cannot finalize a file without completed extraction facts")
            facts = session.execute(text("""SELECT COUNT(*), MIN(request_number), MAX(request_number),
                SUM(request_status IN ('not_attempted','aborted_by_user','interrupted'))
                FROM llm_requests WHERE file_id=:id"""), {"id": file_id}).one()
            if (facts[0] != expected_requests or facts[3]
                    or (expected_requests and (facts[1] != 1 or facts[2] != expected_requests))):
                raise ValueError("Cannot finalize a file with missing or unfinished requests")
            row.processing_complete = True
            row.processing_error = ""
            session.add(row)
            session.commit()

    def record_file_interruption(self, file_id: int, reason: str) -> None:
        with Session(self.sql_engine) as session:
            row = session.get(DatabaseFileRegistry, file_id)
            if row is None or row.processing_complete:
                return
            row.processing_error = replace_lone_surrogates(reason)
            session.add(row)
            session.commit()

    def close(self) -> None:
        self.sql_engine.dispose()
