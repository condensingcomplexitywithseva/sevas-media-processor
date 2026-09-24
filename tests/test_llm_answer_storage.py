# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import schemas
from db_controller import SQLiteDatabaseController
from schemas import RequestOutcome, FileSummary, PageResult


@pytest.fixture
def db(tmp_path):
    controller = SQLiteDatabaseController(tmp_path / "state.db")
    yield controller
    controller.close()


def add_file(db, file_id, rel_path, page_count=3):
    db.handle_file_started(file_id, rel_path, ".png", "Stub")
    for number in range(1, page_count + 1):
        db.handle_frame_saved(file_id, PageResult(
            number, f"{file_id}_page_{number}.jpg", "ok", ""))
    db.handle_file_completed(
        file_id, FileSummary(page_count, f"1-{page_count}", "ok", "done"))
    db.finalize_file(file_id)


def answer_row(**overrides):
    row_type = getattr(schemas, "AnswerRow", None)
    assert row_type is not None, "schemas.AnswerRow must exist (step 14 Phase 3)"
    fields = {
        "page_number": None,
        "raw_model_page_number": "",
        "llm_error": "",
        "values_json": "",
    }
    fields.update(overrides)
    return row_type(**fields)


def request(status="ok", rows=(), number=1, start=1, end=3,
          raw="raw reply", error=""):
    return RequestOutcome(number, tuple(range(start, end + 1)), status, raw, error,
                          answer_rows=tuple(rows))


def read_answers(db_path):
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            "SELECT file_id, request_id, page_id, raw_model_page_number,"
            " llm_error, values_json FROM llm_answers ORDER BY llm_answer_id"
        ).fetchall()
    finally:
        connection.close()


def page_ids(db_path, file_id):
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT page_number, page_id FROM page_log WHERE file_id = ?",
            (file_id,)).fetchall()
    finally:
        connection.close()
    return dict(rows)



def test_answer_rows_land_with_mechanically_joined_page_ids(db, tmp_path):
    add_file(db, 1, "video.mp4", page_count=3)
    db.handle_llm_requests(1, [request(rows=[
        answer_row(page_number=1, raw_model_page_number="1",
                   values_json=json.dumps({"genre": "g1", "answer": "a1"})),
        answer_row(page_number=3, raw_model_page_number="3",
                   values_json=json.dumps({"genre": "g3", "answer": "a3"})),
        answer_row(page_number=2, llm_error="no_row_returned"),
    ])])

    ids = page_ids(tmp_path / "state.db", 1)
    stored = read_answers(tmp_path / "state.db")
    assert len(stored) == 3
    by_error = {row[4]: row for row in stored}
    assert by_error[""][2] in (ids[1], ids[3])
    clean_page_ids = {row[2] for row in stored if row[4] == ""}
    assert clean_page_ids == {ids[1], ids[3]}
    assert by_error["no_row_returned"][2] == ids[2]
    assert by_error["no_row_returned"][5] == ""
    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        request_ids = {row[0] for row in connection.execute(
            "SELECT request_id FROM llm_requests WHERE file_id = 1")}
    finally:
        connection.close()
    assert {row[1] for row in stored} <= request_ids


def test_hallucinated_rows_store_a_null_page_id(db, tmp_path):
    add_file(db, 1, "doc.pdf", page_count=2)
    db.handle_llm_requests(1, [request(end=2, rows=[
        answer_row(page_number=None, raw_model_page_number="9",
                   llm_error="hallucinated_page_number",
                   values_json=json.dumps({"genre": "g9", "answer": "a9"})),
        answer_row(page_number=1, raw_model_page_number="1",
                   values_json=json.dumps({"genre": "g1", "answer": "a1"})),
    ])])

    stored = read_answers(tmp_path / "state.db")
    hallucinated = [row for row in stored
                    if row[4] == "hallucinated_page_number"]
    assert len(hallucinated) == 1
    assert hallucinated[0][2] is None
    assert hallucinated[0][3] == "9"


def test_page_join_is_scoped_to_the_answering_file(db, tmp_path):
    add_file(db, 1, "first.mp4", page_count=2)
    add_file(db, 2, "second.mp4", page_count=2)
    values = json.dumps({"genre": "g", "answer": "a"})
    db.handle_llm_requests(1, [request(end=2, rows=[
        answer_row(page_number=1, raw_model_page_number="1", values_json=values)])])
    db.handle_llm_requests(2, [request(end=2, rows=[
        answer_row(page_number=1, raw_model_page_number="1", values_json=values)])])

    ids_one = page_ids(tmp_path / "state.db", 1)
    ids_two = page_ids(tmp_path / "state.db", 2)
    stored = read_answers(tmp_path / "state.db")
    joined = {row[0]: row[2] for row in stored}
    assert joined[1] == ids_one[1]
    assert joined[2] == ids_two[1]
    assert ids_one[1] != ids_two[1]


def test_failed_requests_store_no_answer_rows(db, tmp_path):
    add_file(db, 1, "img.png", page_count=3)
    db.handle_llm_requests(1, [
        request(status="invalid_json_answer", raw="garbage"),
    ])
    assert read_answers(tmp_path / "state.db") == []



def test_lone_surrogates_in_answer_fields_are_filtered_at_the_gate(db, tmp_path):
    hostile = "evil \ud800 payload"
    add_file(db, 1, "img.png", page_count=1)
    db.handle_llm_requests(1, [request(end=1, rows=[
        answer_row(page_number=1, raw_model_page_number=hostile,
                   values_json=json.dumps({"answer": "x"})[:-2] + hostile + '"}'),
    ])])

    stored = read_answers(tmp_path / "state.db")
    assert len(stored) == 1
    _, _, _, raw_claim, _, values_json = stored[0]
    assert "\ud800" not in raw_claim
    assert "�" in raw_claim
    assert "\ud800" not in values_json
    assert "�" in values_json


def test_answer_rows_survive_alongside_a_surrogate_laden_request_field(db, tmp_path):
    add_file(db, 1, "img.png", page_count=1)
    db.handle_llm_requests(1, [request(
        end=1, raw="reply with \ud800 inside",
        rows=[answer_row(page_number=1, raw_model_page_number="1",
                         values_json=json.dumps({"answer": "clean"}))],
    )])

    stored = read_answers(tmp_path / "state.db")
    assert len(stored) == 1
    assert json.loads(stored[0][5]) == {"answer": "clean"}



def test_answer_row_errors_make_the_run_read_partial_end_to_end(db, tmp_path):
    add_file(db, 1, "img.png", page_count=2)
    db.handle_llm_requests(1, [request(end=2, rows=[
        answer_row(page_number=1, raw_model_page_number="1",
                   values_json=json.dumps({"answer": "a"})),
        answer_row(page_number=2, llm_error="no_row_returned"),
    ])])

    statuses = db.get_file_statuses(1)
    assert statuses["file_to_jpegs_status"] == "ok"
    assert statuses["file_to_llm_status"] == "partial_llm_failure"
    assert statuses["overall_result"] == "partial_fail"


def test_clean_answer_rows_keep_the_file_ok_end_to_end(db, tmp_path):
    add_file(db, 1, "img.png", page_count=2)
    db.handle_llm_requests(1, [request(end=2, rows=[
        answer_row(page_number=1, raw_model_page_number="1",
                   values_json=json.dumps({"answer": "a1"})),
        answer_row(page_number=2, raw_model_page_number="2",
                   values_json=json.dumps({"answer": "a2"})),
    ])])

    assert db.get_file_statuses(1)["overall_result"] == "ok"
