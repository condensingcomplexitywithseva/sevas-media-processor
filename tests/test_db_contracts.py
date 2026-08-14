# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import logging
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from db_controller import SQLiteDatabaseController
from schemas import FileSummary, PageResult, Status


@pytest.fixture
def db(tmp_path):
    controller = SQLiteDatabaseController(tmp_path / "state.db")
    yield controller
    controller.close()


def complete_file(db, file_id, rel_path, status, comment="done"):
    db.handle_file_started(file_id, rel_path, ".png", "Stub")
    db.handle_file_completed(
        file_id, FileSummary(1, "1", "ok", status.value, comment)
    )


def read_registry(db_path):
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT unique_file_id, final_aggregate_status, llm_network_answer"
            " FROM databasefileregistry"
        ).fetchall()
    finally:
        connection.close()
    return {file_id: (status, answer) for file_id, status, answer in rows}



def test_completing_a_missing_file_id_warns_and_writes_nothing(db, caplog):
    with caplog.at_level(logging.WARNING, logger="DatabaseController"):
        db.handle_file_completed(999, FileSummary(1, "1", "ok", Status.OK.value, "x"))
        db.handle_llm_completed(999, "answer", "", Status.OK.value)

    assert db.get_highest_file_id() == 0
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert all("999" in message for message in warnings)



def test_successful_llm_answer_never_clobbers_the_extraction_status(db, tmp_path):
    complete_file(db, 1, "a.png", Status.OK)
    db.handle_llm_completed(1, "a fine answer", "", Status.OK.value)

    complete_file(db, 2, "b.png", Status.PARTIAL_FAILURE)
    db.handle_llm_completed(2, "still answered", "", Status.OK.value)

    registry = read_registry(tmp_path / "state.db")
    assert registry[1] == (Status.OK.value, "a fine answer")
    assert registry[2] == (Status.PARTIAL_FAILURE.value, "still answered")


def test_llm_failures_do_override_the_aggregate_status(db, tmp_path):
    complete_file(db, 1, "a.png", Status.OK)
    db.handle_llm_completed(1, "[TOTAL LLM NETWORK FAILURE]", "boom", Status.LLM_FAILED.value)

    complete_file(db, 2, "b.png", Status.OK)
    db.handle_llm_completed(2, "half an answer", "chunk 2 failed", Status.LLM_PARTIAL.value)

    registry = read_registry(tmp_path / "state.db")
    assert registry[1][0] == Status.LLM_FAILED.value
    assert registry[2][0] == Status.LLM_PARTIAL.value



def test_get_successful_frame_paths_returns_only_ok_frames_of_that_file(db):
    db.handle_file_started(1, "a.png", ".png", "Stub")
    db.handle_frame_saved(1, PageResult(1, "good.jpg", Status.OK.value, ""))
    db.handle_frame_saved(1, PageResult(2, "bad.jpg", Status.FAILURE.value, "torn"))
    db.handle_frame_saved(1, PageResult(3, "static.jpg", Status.SKIPPED.value, "Scene static"))

    db.handle_file_started(2, "b.png", ".png", "Stub")
    db.handle_frame_saved(2, PageResult(1, "other.jpg", Status.OK.value, ""))

    paths = db.get_successful_frame_paths(1, Path("out"))
    assert [p.name for p in paths] == ["good.jpg"]
    assert all(p.parent == Path("out") for p in paths)



def test_resume_query_matches_the_configured_status_list_exactly(db):
    complete_file(db, 1, "finished.png", Status.OK)
    complete_file(db, 2, "broken.png", Status.FAILURE)
    complete_file(db, 3, "half.png", Status.PARTIAL_FAILURE)

    assert db.get_successfully_processed_relative_paths(
        [Status.OK.value]
    ) == {"finished.png"}

    assert db.get_successfully_processed_relative_paths(
        [Status.OK.value, Status.PARTIAL_FAILURE.value]
    ) == {"finished.png", "half.png"}

    assert db.get_successfully_processed_relative_paths([]) == set()



def test_capture_timestamp_is_the_display_string_formatted_once(db, tmp_path):
    db.handle_file_started(1, "clip.mp4", ".mp4", "Stub")
    db.handle_frame_saved(1, PageResult(
        1, "f1.jpg", Status.OK.value, "Extracted exactly at 00:01:33.37.",
        capture_seconds=93.37))
    db.handle_frame_saved(1, PageResult(
        2, "f2.jpg", Status.SKIPPED.value, "Scene static at 01:39:06.00",
        capture_seconds=5945.996))
    db.handle_frame_saved(1, PageResult(
        3, "f3.jpg", Status.FAILURE.value, "Seek error"))

    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        page_rows = connection.execute(
            "SELECT capture_timestamp, llm_answer_json FROM databasepagelog"
            " ORDER BY page_or_frame_number"
        ).fetchall()
        registry_json = connection.execute(
            "SELECT llm_answer_json FROM databasefileregistry"
        ).fetchall()
    finally:
        connection.close()

    assert page_rows == [
        ("00:01:33.37", ""),
        ("01:39:06.00", ""),
        ("", ""),
    ]
    assert registry_json == [("",)]
