# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import logging
import re
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from db_controller import SQLiteDatabaseController
from schemas import (
    RequestOutcome,
    RequestStatus,
    FileSummary,
    OverallResult,
    PageResult,
    RangeStatus,
    Status,
)


@pytest.fixture
def db(tmp_path):
    controller = SQLiteDatabaseController(tmp_path / "state.db")
    yield controller
    controller.close()


def add_file(db, file_id, rel_path, page_statuses=("ok",),
             range_status="ok", comment="done"):
    db.handle_file_started(file_id, rel_path, ".png", "Stub")
    for number, status in enumerate(page_statuses, start=1):
        db.handle_frame_saved(file_id, PageResult(
            number, f"{file_id}_page_{number}.jpg", status, ""))
    db.handle_file_completed(
        file_id, FileSummary(len(page_statuses), "1", range_status, comment))
    db.finalize_file(file_id)


def read_view(db_path, view):
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            f"SELECT file_id, {view} FROM {view}").fetchall()
    finally:
        connection.close()
    return dict(rows)



def test_completing_a_missing_file_id_warns_and_writes_nothing(db, caplog):
    with caplog.at_level(logging.WARNING, logger="DatabaseController"):
        db.handle_file_completed(999, FileSummary(1, "1", "ok", "x"))

    assert db.get_highest_file_id() == 0
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "999" in warnings[0]



def test_file_to_jpegs_status_derives_from_page_rows_and_range_status(db, tmp_path):
    add_file(db, 1, "clean.png", ("ok", "ok"))
    add_file(db, 2, "mixed.png", ("ok", "failure"))
    add_file(db, 3, "broken.png", ("failure", "failure"))
    add_file(db, 4, "bypassed.png", (), range_status="skipped")
    add_file(db, 5, "static.png", ("ok", "skipped"))
    add_file(db, 6, "aborted.png", ("ok", "ok"), range_status="failure")
    db.handle_file_started(7, "vanished.png", ".png", "Stub")

    assert read_view(tmp_path / "state.db", "file_to_jpegs_status") == {
        1: "ok",
        2: "partial_processing_failure",
        3: "processing_failure",
        4: "skipped",
        5: "ok",
        6: "partial_processing_failure",
        7: "processing_failure",
    }


def test_file_to_llm_status_derives_from_request_rows(db, tmp_path):
    add_file(db, 1, "no_ai.png")
    add_file(db, 2, "ai_ok.png")
    db.handle_llm_requests(2, [RequestOutcome(1, (1,), "ok", "answer", "")])
    add_file(db, 3, "ai_mixed.png")
    db.handle_llm_requests(3, [
        RequestOutcome(1, tuple(range(1, 6)), "ok", "answer", ""),
        RequestOutcome(2, (6, 7, 8, 9), "network_failure", "", "boom"),
    ])
    add_file(db, 4, "ai_dead.png")
    db.handle_llm_requests(4, [
        RequestOutcome(1, tuple(range(1, 6)), "network_failure", "", "boom"),
        RequestOutcome(2, (6, 7, 8, 9), "token_limit_exceeded", "", "cut"),
    ])

    assert read_view(tmp_path / "state.db", "file_to_llm_status") == {
        1: "skipped",
        2: "ok",
        3: "partial_llm_failure",
        4: "llm_failure",
    }


def test_overall_result_combines_the_two_legs(db, tmp_path):
    add_file(db, 1, "clean_no_ai.png")
    add_file(db, 2, "clean_ai_ok.png")
    db.handle_llm_requests(2, [RequestOutcome(1, (1,), "ok", "answer", "")])
    add_file(db, 3, "clean_ai_failed.png")
    db.handle_llm_requests(3, [RequestOutcome(1, (1,), "network_failure", "", "boom")])
    add_file(db, 4, "jpegs_partial.png", ("ok", "failure"))
    add_file(db, 5, "jpegs_dead.png", ("failure",))
    add_file(db, 6, "bypassed.png", (), range_status="skipped")

    assert read_view(tmp_path / "state.db", "overall_result") == {
        1: "ok",
        2: "ok",
        3: "partial_fail",
        4: "partial_fail",
        5: "fail",
        6: "skipped",
    }
    assert db.get_file_statuses(3) == {
        "file_to_jpegs_status": "ok",
        "file_to_llm_status": "llm_failure",
        "overall_result": "partial_fail",
    }
    assert db.get_file_statuses(999) == {}



def test_run_configuration_is_one_row_the_last_run_wrote(db, tmp_path):
    assert db.get_run_configuration() is None
    db.record_run_configuration(True, "table_per_page", ("genre", "answer"))
    db.record_run_configuration(True, "table_per_page", ("genre", "answer"))
    recorded = db.get_run_configuration()
    assert recorded is not None
    assert (recorded.ai_enabled, recorded.output_mode, recorded.declared_columns) == (
        True, "table_per_page", ("genre", "answer"))
    db.record_run_configuration(False, "", ())
    recorded = db.get_run_configuration()
    assert recorded is not None and recorded.declared_columns == () and not recorded.ai_enabled
    assert recorded.output_mode == ""
    with pytest.raises(ValueError):
        db.record_run_configuration(False, "table_per_file", ())
    with pytest.raises(ValueError):
        db.record_run_configuration(False, "", ("answer",))
    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        assert connection.execute("SELECT COUNT(*) FROM run_configuration").fetchone() == (1,)
    finally:
        connection.close()



def test_file_system_text_passes_the_surrogate_gate_at_every_store_handler(db, tmp_path):
    db.handle_file_started(1, "photo_\ud800.png", ".png", "Stub")
    db.handle_frame_saved(1, PageResult(1, "1_photo_\udfff.jpg", "failure",
                                        "cannot identify 'photo_\ud800.png'"))
    db.handle_file_completed(1, FileSummary(1, "1\ud800", "failure", "failed on photo_\ud800.png"))
    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        registry = connection.execute(
            "SELECT file_path, page_range, file_to_jpegs_comment FROM file_registry").fetchone()
        page = connection.execute(
            "SELECT output_file, page_to_jpeg_comment FROM page_log").fetchone()
    finally:
        connection.close()
    assert registry == ("photo_�.png", "1�", "failed on photo_�.png")
    assert page == ("1_photo_�.jpg", "cannot identify 'photo_�.png'")



def test_handle_llm_requests_stores_one_row_per_outcome(db, tmp_path):
    add_file(db, 1, "clip.mp4")
    db.handle_llm_requests(1, [
        RequestOutcome(1, tuple(range(1, 11)), "ok", "first answer", ""),
        RequestOutcome(2, tuple(range(11, 16)), "invalid_json_answer", "not json", "parse notes"),
    ])
    db.handle_llm_requests(1, [])

    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        rows = connection.execute(
            "SELECT file_id, request_number, pages,"
            " request_status, raw_llm_answer, llm_network_error"
            " FROM llm_requests ORDER BY request_id").fetchall()
    finally:
        connection.close()
    assert rows == [
        (1, 1, "1-10", "ok", "first answer", ""),
        (1, 2, "11-15", "invalid_json_answer", "not json", "parse notes"),
    ]


def test_not_attempted_markers_keep_a_cut_short_ai_leg_out_of_resume_skip(db, tmp_path):
    add_file(db, 1, "cut_short.mp4", ("ok", "ok", "ok"))
    db.handle_llm_requests(1, [
        RequestOutcome(1, (1, 2), "ok", "answer one", ""),
        RequestOutcome(2, (3,), "not_attempted", "",
                     "Not attempted: run aborted by user before this request."),
    ])
    add_file(db, 2, "never_started.mp4", ("ok",))
    db.handle_llm_requests(2, [
        RequestOutcome(1, (1,), "not_attempted", "",
                     "Not attempted: run aborted by user before the AI leg."),
    ])

    assert read_view(tmp_path / "state.db", "file_to_llm_status") == {
        1: "partial_llm_failure",
        2: "llm_failure",
    }
    assert read_view(tmp_path / "state.db", "overall_result") == {
        1: "partial_fail",
        2: "partial_fail",
    }
    assert set(db.get_no_retry_sources(["ok"])) == set()


def test_handle_llm_requests_replaces_lone_surrogates_at_the_storage_gate(db, tmp_path):
    add_file(db, 1, "hostile.mp4")
    db.handle_llm_requests(1, [
        RequestOutcome(1, (1,), "ok", "bad\ud800answer", "note\udfffnote"),
    ])

    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        row = connection.execute(
            "SELECT raw_llm_answer, llm_network_error FROM llm_requests").fetchone()
    finally:
        connection.close()
    assert row == ("bad�answer", "note�note")



def test_get_successful_frames_returns_only_ok_frames_of_that_file(db):
    db.handle_file_started(1, "a.png", ".png", "Stub")
    db.handle_frame_saved(1, PageResult(1, "good.jpg", Status.OK.value, ""))
    db.handle_frame_saved(1, PageResult(2, "bad.jpg", Status.FAILURE.value, "torn"))
    db.handle_frame_saved(1, PageResult(3, "static.jpg", Status.SKIPPED.value, "Scene static"))
    db.handle_frame_saved(1, PageResult(4, "clip.jpg", Status.OK.value, "",
                                        capture_seconds=93.37))

    db.handle_file_started(2, "b.png", ".png", "Stub")
    db.handle_frame_saved(2, PageResult(1, "other.jpg", Status.OK.value, ""))

    frames = db.get_successful_frames(1, Path("out"))
    assert [(number, path.name, ts) for number, path, ts in frames] == [
        (1, "good.jpg", ""), (4, "clip.jpg", "00:01:33.37")]
    assert all(path.parent == Path("out") for _, path, _ in frames)



def test_resume_query_matches_the_configured_result_list_exactly(db):
    add_file(db, 1, "finished.png", ("ok",))
    add_file(db, 2, "broken.png", ("failure",))
    add_file(db, 3, "half.png", ("ok", "failure"))

    assert set(db.get_no_retry_sources(["ok"])) == {"finished.png"}

    assert set(db.get_no_retry_sources(["ok", "partial_fail"])) == {
        "finished.png", "half.png"}

    assert set(db.get_no_retry_sources([])) == set()



def test_video_frame_timestamp_is_the_display_string_formatted_once(db, tmp_path):
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
            "SELECT video_frame_timestamp FROM page_log ORDER BY page_number"
        ).fetchall()
    finally:
        connection.close()

    assert page_rows == [("00:01:33.37",), ("01:39:06.00",), ("",)]




def read_llm_view_and_skip_set(db, tmp_path):
    return (read_view(tmp_path / "state.db", "file_to_llm_status"),
            set(db.get_no_retry_sources(["ok"])))


def insert_answer_row(db_path, request_id, file_id, llm_error, values_json=""):
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO llm_answers (request_id, file_id, page_id,"
            " raw_model_page_number, llm_error, values_json)"
            " VALUES (?, ?, NULL, '', ?, ?)",
            (request_id, file_id, llm_error, values_json))
        connection.commit()
    finally:
        connection.close()


def test_stored_schema_and_views_match_the_declared_map(db, tmp_path):
    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        kinds = dict(connection.execute(
            "SELECT name, type FROM sqlite_master"
            " WHERE type IN ('table', 'view')").fetchall())
        columns = {
            table: {row[1] for row in connection.execute(
                f"PRAGMA table_info({table})")}
            for table in ("file_registry", "page_log",
                          "llm_requests", "llm_answers", "run_configuration")
        }
    finally:
        connection.close()

    for table in ("file_registry", "page_log", "llm_requests", "llm_answers",
                  "run_configuration"):
        assert kinds.get(table) == "table"
    assert columns["run_configuration"] == {
        "configuration_id", "ai_enabled", "output_mode", "declared_columns", "source_root", "processing_digest"}
    for view in ("file_to_jpegs_status", "file_to_llm_status", "overall_result"):
        assert kinds.get(view) == "view"

    assert columns["file_registry"] == {
        "file_id", "file_path", "file_ext", "total_pages", "page_range",
        "range_status", "file_to_jpegs_comment", "processing_complete", "ai_enabled", "processing_error",
        "source_size", "source_mtime_ns", "source_sha256"}
    assert columns["page_log"] == {
        "page_id", "file_id", "page_number", "output_file",
        "page_to_jpeg_status", "page_to_jpeg_comment",
        "video_frame_timestamp"}
    assert columns["llm_requests"] == {
        "request_id", "file_id", "request_number", "pages",
        "request_status", "raw_llm_answer", "llm_network_error"}
    assert columns["llm_answers"] == {
        "llm_answer_id", "request_id", "file_id", "page_id",
        "raw_model_page_number", "llm_error", "values_json"}


def test_status_vocabularies_are_lowercase_snake_case():
    for enum_type in (Status, RequestStatus, OverallResult, RangeStatus):
        for member in enum_type:
            assert re.fullmatch(r"[a-z]+(_[a-z]+)*", member.value), (
                f"{enum_type.__name__}.{member.name} stores "
                f"{member.value!r} — not lowercase snake_case")


def test_status_vocabularies_carry_only_producible_or_declared_values():
    assert {m.value for m in Status} == {"ok", "failure", "skipped"}
    assert {m.value for m in RangeStatus} == {
        "ok", "truncated", "skipped", "partial_skip", "failure"}
    assert {m.value for m in OverallResult} == {
        "ok", "partial_fail", "fail", "skipped"}
    assert {m.value for m in RequestStatus} == {
        "ok", "aborted_by_user", "encoding_failure", "token_limit_exceeded",
        "network_failure", "provider_reply_parse_error",
        "invalid_json_answer", "not_attempted", "interrupted"}


@pytest.mark.parametrize("status", [
    "aborted_by_user", "encoding_failure", "token_limit_exceeded",
    "network_failure", "provider_reply_parse_error", "invalid_json_answer",
    "not_attempted",
])
def test_every_non_ok_request_status_alone_reads_llm_failure(db, tmp_path, status):
    add_file(db, 1, "one_bad_request.png")
    db.handle_llm_requests(1, [RequestOutcome(1, (1,), status, "", "detail")])

    llm_view, skip_set = read_llm_view_and_skip_set(db, tmp_path)
    assert llm_view == {1: "llm_failure"}
    assert read_view(tmp_path / "state.db", "overall_result") == {1: "partial_fail"}
    assert skip_set == set()


def test_answer_row_errors_flip_a_clean_request_leg_off_ok(db, tmp_path):
    add_file(db, 1, "clean.png")
    db.handle_llm_requests(1, [RequestOutcome(1, (1,), "ok", "answer", "")])
    insert_answer_row(tmp_path / "state.db", 1, 1, llm_error="",
                      values_json='{"answer": "fine"}')

    add_file(db, 2, "missing_row.png")
    db.handle_llm_requests(2, [RequestOutcome(2, (1,), "ok", "answer", "")])
    insert_answer_row(tmp_path / "state.db", 2, 2, llm_error="no_row_returned")

    llm_view, skip_set = read_llm_view_and_skip_set(db, tmp_path)
    assert llm_view == {1: "ok", 2: "partial_llm_failure"}
    assert read_view(tmp_path / "state.db", "overall_result") == {
        1: "ok", 2: "partial_fail"}
    assert skip_set == {"clean.png"}


def test_all_scene_static_pages_still_read_ok(db, tmp_path):
    add_file(db, 1, "static_only.mp4", ("skipped", "skipped"))
    assert read_view(tmp_path / "state.db", "file_to_jpegs_status") == {1: "ok"}
    assert read_view(tmp_path / "state.db", "overall_result") == {1: "ok"}


def test_jpegs_failure_dominates_even_a_clean_ai_leg(db, tmp_path):
    add_file(db, 1, "dead.png", ("failure",))
    db.handle_llm_requests(1, [RequestOutcome(1, (1,), "ok", "answer", "")])

    assert db.get_file_statuses(1) == {
        "file_to_jpegs_status": "processing_failure",
        "file_to_llm_status": "ok",
        "overall_result": "fail",
    }


def test_torn_run_with_saved_pages_must_not_read_ok(db, tmp_path):
    db.handle_file_started(1, "torn.pdf", ".pdf", "Stub")
    db.handle_frame_saved(1, PageResult(1, "1_page_1.jpg", Status.OK.value, ""))
    db.handle_frame_saved(1, PageResult(2, "1_page_2.jpg", Status.OK.value, ""))

    assert db.get_file_statuses(1) == {
        "file_to_jpegs_status": "partial_processing_failure",
        "file_to_llm_status": "skipped",
        "overall_result": "partial_fail",
    }
    assert set(db.get_no_retry_sources(["ok"])) == set()


def test_completing_with_an_empty_range_status_is_refused(db):
    db.handle_file_started(1, "sloppy.png", ".png", "Stub")
    with pytest.raises(AssertionError, match="range_status"):
        db.handle_file_completed(1, FileSummary(1, "1", "", "done"))


def test_views_are_recreated_on_every_init(db, tmp_path):
    add_file(db, 1, "clean.png", ("ok",))
    db.close()

    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        connection.execute("DROP VIEW file_to_jpegs_status")
        connection.execute(
            "CREATE VIEW file_to_jpegs_status AS"
            " SELECT file_id, 'stale-definition' AS file_to_jpegs_status"
            " FROM file_registry")
        connection.commit()
    finally:
        connection.close()

    reopened = SQLiteDatabaseController(tmp_path / "state.db")
    try:
        assert reopened.get_file_statuses(1)["file_to_jpegs_status"] == "ok"
    finally:
        reopened.close()


def test_a_retried_path_with_a_later_ok_row_is_skipped_thereafter(db):
    add_file(db, 1, "flaky.png", ("ok", "failure"))
    add_file(db, 2, "flaky.png", ("ok", "ok"))

    assert set(db.get_no_retry_sources(["ok"])) == {"flaky.png"}


@pytest.mark.parametrize("status", ["not_attempted", "aborted_by_user", "interrupted"])
def test_unfinished_request_cannot_be_finalized_as_a_completed_file(db, status):
    db.handle_file_started(1, "unfinished.png", ".png", "Generated", ai_enabled=True)
    db.handle_frame_saved(1, PageResult(1, "1.jpg", "ok", ""))
    db.handle_file_completed(1, FileSummary(1, "1", "ok", "done"))
    db.handle_llm_requests(1, [RequestOutcome(1, (1,), status, "", "unfinished")])
    with pytest.raises(ValueError, match="unfinished"):
        db.finalize_file(1, 1)
    assert db.get_file_statuses(1)["overall_result"] == "partial_fail"
    assert set(db.get_no_retry_sources(["ok"])) == set()


def test_completion_requires_every_expected_request_and_is_idempotent(db, tmp_path):
    db.handle_file_started(1, "unfinished.png", ".png", "Generated", ai_enabled=True)
    db.handle_frame_saved(1, PageResult(1, "1.jpg", "ok", ""))
    db.handle_file_completed(1, FileSummary(1, "1", "ok", "done"))
    with pytest.raises(ValueError, match="missing"):
        db.finalize_file(1, 1)
    db.handle_llm_requests(1, [RequestOutcome(1, (1,), "network_failure", "", "refused")])
    db.finalize_file(1, 1)
    db.finalize_file(1, 1)
    with sqlite3.connect(tmp_path / "state.db") as connection:
        assert connection.execute("SELECT processing_complete FROM file_registry").fetchall() == [(1,)]
        assert connection.execute("SELECT COUNT(*) FROM llm_requests").fetchone() == (1,)
    assert db.get_file_statuses(1)["overall_result"] == "partial_fail"
