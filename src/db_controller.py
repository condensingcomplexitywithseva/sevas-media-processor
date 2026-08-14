# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import logging
import sqlite3
from sqlmodel import SQLModel, Session, create_engine, select, col
from sqlalchemy import event
from pathlib import Path

from db_schema import DatabaseFileRegistry, DatabasePageLog
from fs_utils import format_hms, get_safe_path
from schemas import Status, PageResult, FileSummary

database_logger = logging.getLogger("DatabaseController")

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

        SQLModel.metadata.create_all(self.sql_engine)
        database_logger.info(f"SQLite database initialized successfully at: {self.database_path}")

    def get_highest_file_id(self) -> int:
        with Session(self.sql_engine) as active_session:
            statement = select(DatabaseFileRegistry.unique_file_id).order_by(col(DatabaseFileRegistry.unique_file_id).desc())
            highest_id = active_session.exec(statement).first()
            return highest_id if highest_id is not None else 0

    def handle_file_started(self, unique_file_id: int, relative_file_path: str, detected_extension: str, pipeline_name: str) -> None:
        with Session(self.sql_engine) as active_session:
            new_registry_entry = DatabaseFileRegistry(
                unique_file_id=unique_file_id,
                relative_file_path=relative_file_path,
                original_extension=detected_extension,
                total_discovered_pages=0,
                final_aggregate_comment=f"Assigned to: {pipeline_name}"
            )
            active_session.add(new_registry_entry)
            active_session.commit()

    def handle_frame_saved(self, unique_file_id: int, page_result: PageResult) -> None:
        with Session(self.sql_engine) as active_session:
            new_page_log = DatabasePageLog(
                parent_file_id=unique_file_id,
                page_or_frame_number=page_result.page_number,
                saved_filename=page_result.output_filename,
                execution_status=page_result.success,
                execution_comment=page_result.comment,
                capture_timestamp=(
                    format_hms(page_result.capture_seconds)
                    if page_result.capture_seconds is not None else ""
                )
            )
            active_session.add(new_page_log)
            active_session.commit()

    def handle_file_completed(self, unique_file_id: int, file_summary: FileSummary) -> None:
        with Session(self.sql_engine) as active_session:
            existing_record = active_session.get(DatabaseFileRegistry, unique_file_id)
            if existing_record:
                existing_record.total_discovered_pages = file_summary.total_discovered_pages
                existing_record.final_aggregate_status = file_summary.final_aggregate_status
                existing_record.final_aggregate_comment = file_summary.final_aggregate_comment
                existing_record.applied_range_string = file_summary.applied_range_string
                existing_record.range_status_code = file_summary.range_status_code

                active_session.add(existing_record)
                active_session.commit()
            else:
                database_logger.warning(
                    f"handle_file_completed: no registry record found for file ID {unique_file_id}; completion not recorded."
                )

    def handle_llm_completed(self, unique_file_id: int, answer: str, error: str, status_override: str) -> None:
        with Session(self.sql_engine) as active_session:
            existing_record = active_session.get(DatabaseFileRegistry, unique_file_id)
            if existing_record:
                existing_record.llm_network_answer = answer
                existing_record.llm_network_error = error

                if status_override in [Status.LLM_FAILED.value, Status.LLM_PARTIAL.value]:
                    existing_record.final_aggregate_status = status_override

                active_session.add(existing_record)
                active_session.commit()
            else:
                database_logger.warning(
                    f"handle_llm_completed: no registry record found for file ID {unique_file_id}; AI result not recorded."
                )

    def get_successfully_processed_relative_paths(self, no_retry_statuses: list[str]) -> set[str]:
        with Session(self.sql_engine) as active_session:
            statement = select(DatabaseFileRegistry.relative_file_path).where(
                col(DatabaseFileRegistry.final_aggregate_status).in_(no_retry_statuses)
            )
            results = active_session.exec(statement).all()
            return set(results)

    def get_successful_frame_paths(self, target_file_id: int, base_output_folder: Path) -> list[Path]:
        with Session(self.sql_engine) as active_session:
            statement = select(DatabasePageLog.saved_filename).where(
                DatabasePageLog.parent_file_id == target_file_id,
                DatabasePageLog.execution_status == Status.OK.value
            )
            saved_filenames = active_session.exec(statement).all()
            return [base_output_folder / filename for filename in saved_filenames]

    def close(self) -> None:
        if hasattr(self, 'sql_engine') and self.sql_engine:
            self.sql_engine.dispose()
