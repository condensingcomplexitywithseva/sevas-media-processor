# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import csv
import json
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import data_exporter
from data_exporter import (
    DB_ONLY_FIELDS,
    EXCEL_MAX_CELL_CHARS,
    LLM_ANSWERS_REPORT_COLUMNS,
    LLM_REQUESTS_REPORT_COLUMNS,
    PAGE_LOG_REPORT_COLUMNS,
    REGISTRY_REPORT_COLUMNS,
    SQLiteDataExporter,
    TRUNCATION_MARKER,
)
from db_controller import SQLiteDatabaseController
from db_schema import (
    DatabaseFileRegistry,
    DatabaseLLMAnswer,
    DatabaseLLMRequest,
    DatabaseRunConfiguration,
    DatabasePageLog,
)
from schemas import RequestOutcome, FileSummary, PageResult, Status

import openpyxl



FILES = [
    (1, "docs/report.pdf", ".pdf", 3, "done", "1-3", "ok"),
    (2, "pics/photo.png", ".png", 1, "corrupt header", "", "failure"),
    (3, "сканы/отчёт №3, «финал».pdf", ".pdf", 2, 'проверка, "кавычки" и\nперенос строки', "1", "ok"),
]

PAGES = [
    (1, 1, "0001_p001.jpg", Status.OK.value, "", None),
    (1, 2, "0001_p002.jpg", Status.OK.value, "slow decode", None),
    (1, 3, "0001_p003.jpg", Status.FAILURE.value, "render failed", None),
    (2, 1, "", Status.FAILURE.value, "corrupt header", None),
    (3, 1, "0003_p001.jpg", Status.OK.value, "медленно, но «ок»", None),
    (3, 2, "0003_p002.jpg", Status.OK.value,
     "Extracted exactly at 00:01:33.37. Saved.", 93.37),
]

EXPECTED_CAPTURE_CELLS = ["", "", "", "", "", "00:01:33.37"]

EXPECTED_VIEW_CELLS = {
    1: ("partial_processing_failure", "skipped", "partial_fail"),
    2: ("processing_failure", "skipped", "fail"),
    3: ("ok", "skipped", "ok"),
}


def seed_database(db_path):
    controller = SQLiteDatabaseController(db_path)
    for file_id, rel_path, ext, pages, comment, range_str, range_status in FILES:
        controller.handle_file_started(file_id, rel_path, ext, "TestPipeline")
        controller.handle_file_completed(
            file_id,
            FileSummary(
                total_pages=pages,
                page_range=range_str,
                range_status=range_status,
                file_to_jpegs_comment=comment,
            ),
        )
    for file_id, page_number, filename, status, comment, capture_seconds in PAGES:
        controller.handle_frame_saved(
            file_id,
            PageResult(page_number, filename, status, comment,
                       capture_seconds=capture_seconds),
        )
    for file_id, *_ in FILES:
        controller.finalize_file(file_id)
    controller.record_run_configuration(False, "", ())
    controller.close()


@pytest.fixture
def exported(tmp_path):
    db_path = tmp_path / "application_state.db"
    seed_database(db_path)

    exporter = SQLiteDataExporter(db_path)
    exporter.export_all_formats(tmp_path)
    return tmp_path


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_one(directory, pattern):
    matches = list(directory.glob(pattern))
    assert len(matches) == 1, f"expected exactly one {pattern}, got {matches}"
    return matches[0]



def test_every_db_field_is_classified_into_a_report_or_db_only():
    partitions = [
        (DatabaseFileRegistry, "file_registry", REGISTRY_REPORT_COLUMNS),
        (DatabasePageLog, "page_log", PAGE_LOG_REPORT_COLUMNS),
        (DatabaseLLMRequest, "llm_requests", LLM_REQUESTS_REPORT_COLUMNS),
        (DatabaseLLMAnswer, "llm_answers", LLM_ANSWERS_REPORT_COLUMNS),
        (DatabaseRunConfiguration, "run_configuration", []),
    ]
    for model, table_name, report_columns in partitions:
        stored = set(model.model_fields)
        reported = set(report_columns)
        db_only = set(DB_ONLY_FIELDS.get(table_name, ()))
        unclassified = stored - reported - db_only
        assert not unclassified, (
            f"{table_name}: field(s) {sorted(unclassified)} are neither in the "
            f"report columns nor declared DB-only — classify them")
        assert not (reported & db_only), (
            f"{table_name}: a field cannot be both reported and DB-only")
        view_columns = {"file_to_jpegs_status", "file_to_llm_status",
                        "overall_result"}
        phantom = reported - stored - view_columns
        assert not phantom, (
            f"{table_name}: report column(s) {sorted(phantom)} exist neither "
            f"in the schema nor among the status views")



LEAKY_MESSAGE = (
    "Catastrophic Image error: cannot identify image file "
    "'\\\\\\\\?\\\\C:\\\\pics\\\\holiday.jpeg'"
)


def test_no_exported_cell_can_carry_the_long_path_prefix(tmp_path):
    db_path = tmp_path / "leak_check.db"
    controller = SQLiteDatabaseController(db_path)
    controller.handle_file_started(1, "pics/holiday.jpeg", ".jpeg", "StaticImagePipeline")
    controller.handle_file_completed(
        1,
        FileSummary(
            total_pages=0,
            page_range="",
            range_status=Status.FAILURE.value,
            file_to_jpegs_comment=LEAKY_MESSAGE,
        ),
    )
    controller.handle_frame_saved(1, PageResult(1, "", Status.FAILURE.value, LEAKY_MESSAGE))
    controller.record_run_configuration(False, "", ())
    controller.close()

    exporter = SQLiteDataExporter(db_path)
    exporter.export_all_formats(tmp_path)

    registry = read_csv(find_one(tmp_path, "file_registry_*.csv"))
    pages = read_csv(find_one(tmp_path, "page_log_*.csv"))
    assert "\\\\?\\" not in registry[0]["file_to_jpegs_comment"]
    assert "\\\\?\\" not in pages[0]["page_to_jpeg_comment"]
    assert "holiday.jpeg" in registry[0]["file_to_jpegs_comment"]

    workbook = openpyxl.load_workbook(find_one(tmp_path, "database_export_*.xlsx"))
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(values_only=True):
            for cell in row:
                assert "\\\\?\\" not in str(cell)



def test_csv_registry_round_trips_seeded_content(exported):
    rows = read_csv(find_one(exported, "file_registry_*.csv"))

    assert [r["file_path"] for r in rows] == [f[1] for f in FILES]
    for row, (file_id, _rel_path, ext, pages, comment, range_str,
              range_status) in zip(rows, FILES, strict=True):
        assert row["file_id"] == str(file_id)
        assert row["file_ext"] == ext
        assert row["total_pages"] == str(pages)
        assert row["file_to_jpegs_comment"] == comment
        assert row["page_range"] == range_str
        assert row["range_status"] == range_status
        jpegs, llm, overall = EXPECTED_VIEW_CELLS[file_id]
        assert row["file_to_jpegs_status"] == jpegs
        assert row["file_to_llm_status"] == llm
        assert row["overall_result"] == overall
    assert list(rows[0]) == REGISTRY_REPORT_COLUMNS


def test_csv_page_log_round_trips_seeded_content(exported):
    rows = read_csv(find_one(exported, "page_log_*.csv"))

    assert len(rows) == len(PAGES)
    for row, (file_id, page_number, filename, status, comment, _), expected_hms \
            in zip(rows, PAGES, EXPECTED_CAPTURE_CELLS, strict=True):
        assert row["file_id"] == str(file_id)
        assert row["page_number"] == str(page_number)
        assert row["output_file"] == filename
        assert row["page_to_jpeg_status"] == status
        assert row["page_to_jpeg_comment"] == comment
        assert row["video_frame_timestamp"] == expected_hms
    assert list(rows[0]) == PAGE_LOG_REPORT_COLUMNS



def test_llm_reports_absent_when_no_ai_ran(exported):
    assert not list(exported.glob("llm_requests_*.csv"))
    assert not list(exported.glob("llm_answers_*.csv"))
    workbook = openpyxl.load_workbook(find_one(exported, "database_export_*.xlsx"))
    assert workbook.sheetnames == ["Summary", "Results", "Details>>>", "Master Registry", "Page Log"]


def test_llm_requests_report_round_trips_request_rows(tmp_path):
    db_path = tmp_path / "application_state.db"
    seed_database(db_path)
    controller = SQLiteDatabaseController(db_path)
    controller.handle_llm_requests(1, [
        RequestOutcome(1, (1, 2), "ok", 'ответ, "с кавычками"\nи переносом', ""),
        RequestOutcome(2, (3,), "network_failure", "", "Request 2 Network Failure: boom"),
    ])
    controller.record_run_configuration(True, 'table_per_file', ("answer",))
    controller.close()

    exporter = SQLiteDataExporter(db_path)
    exporter.export_all_formats(tmp_path)

    rows = read_csv(find_one(tmp_path, "llm_requests_*.csv"))
    assert list(rows[0]) == LLM_REQUESTS_REPORT_COLUMNS
    assert [(r["file_id"], r["request_number"], r["pages"],
             r["request_status"]) for r in rows] == [
        ("1", "1", "1-2", "ok"),
        ("1", "2", "3", "network_failure"),
    ]
    assert rows[0]["raw_llm_answer"] == 'ответ, "с кавычками"\nи переносом'
    assert rows[1]["llm_network_error"] == "Request 2 Network Failure: boom"
    assert list(tmp_path.glob("llm_answers_*.csv"))

    workbook = openpyxl.load_workbook(find_one(tmp_path, "database_export_*.xlsx"))
    assert workbook.sheetnames == [
        "Summary", "Results", "Details>>>", "Master Registry", "Page Log", "LLM Requests", "LLM Answers"]
    sheet_rows = list(workbook["LLM Requests"].iter_rows(values_only=True))
    assert list(sheet_rows[0]) == LLM_REQUESTS_REPORT_COLUMNS
    assert len(sheet_rows) == 3


def test_control_characters_in_answers_do_not_kill_the_xlsx_export(tmp_path):
    db_path = tmp_path / "application_state.db"
    seed_database(db_path)
    controller = SQLiteDatabaseController(db_path)
    controller.handle_llm_requests(1, [
        RequestOutcome(1, (1, 2), "ok", "bad\x00answer\x08", "note\x1fnote"),
    ])
    controller.record_run_configuration(True, 'table_per_file', ("answer",))
    controller.close()

    exporter = SQLiteDataExporter(db_path)
    exporter.export_all_formats(tmp_path)

    workbook = openpyxl.load_workbook(find_one(tmp_path, "database_export_*.xlsx"))
    sheet_rows = list(workbook["LLM Requests"].iter_rows(values_only=True))
    by_column = dict(zip(sheet_rows[0], sheet_rows[1], strict=True))
    assert by_column["raw_llm_answer"] == "bad�answer�"
    assert by_column["llm_network_error"] == "note�note"

    rows = read_csv(find_one(tmp_path, "llm_requests_*.csv"))
    assert rows[0]["raw_llm_answer"] == "bad\x00answer\x08"


def test_llm_answers_report_appears_when_rows_exist(tmp_path):
    db_path = tmp_path / "application_state.db"
    seed_database(db_path)
    controller = SQLiteDatabaseController(db_path)
    controller.handle_llm_requests(1, [RequestOutcome(1, (1, 2), "ok", "answer", "")])
    controller.record_run_configuration(True, 'table_per_file', ("answer",))
    controller.close()

    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "INSERT INTO llm_answers (request_id, file_id, page_id,"
            " raw_model_page_number, llm_error, values_json)"
            " VALUES (1, 1, NULL, '99', 'hallucinated_page_number',"
            " '{\"answer\": \"x\"}')")
        connection.commit()
    finally:
        connection.close()

    exporter = SQLiteDataExporter(db_path)
    exporter.export_all_formats(tmp_path)

    rows = read_csv(find_one(tmp_path, "llm_answers_*.csv"))
    assert list(rows[0]) == [*LLM_ANSWERS_REPORT_COLUMNS, "llm_answer"]
    assert rows[0]["request_id"] == "1"
    assert rows[0]["file_id"] == "1"
    assert rows[0]["page_id"] == ""
    assert rows[0]["raw_model_page_number"] == "99"
    assert rows[0]["llm_error"] == "hallucinated_page_number"

    workbook = openpyxl.load_workbook(find_one(tmp_path, "database_export_*.xlsx"))
    assert workbook.sheetnames == [
        "Summary", "Results", "Details>>>", "Master Registry", "Page Log", "LLM Requests", "LLM Answers"]
    sheet_rows = list(workbook["LLM Answers"].iter_rows(values_only=True))
    assert list(sheet_rows[0]) == [*LLM_ANSWERS_REPORT_COLUMNS, "llm_answer"]

    registry = read_csv(find_one(tmp_path, "file_registry_*.csv"))
    assert registry[0]["file_to_llm_status"] == "partial_llm_failure"
    assert registry[0]["overall_result"] == "partial_fail"


def test_a_megabyte_of_csv_delimiter_soup_survives_storage_and_export(tmp_path):
    soup_line = 'строка, "quoted";\tcolumn\r\nsecond half'
    payload = soup_line * 20_000 + "\x01tail👍"

    db_path = tmp_path / "application_state.db"
    seed_database(db_path)
    controller = SQLiteDatabaseController(db_path)
    controller.handle_llm_requests(1, [RequestOutcome(1, (1, 2), "ok", payload, "")])
    controller.record_run_configuration(True, 'table_per_file', ("answer",))
    controller.close()

    exporter = SQLiteDataExporter(db_path)
    exporter.export_all_formats(tmp_path)

    old_limit = csv.field_size_limit(len(payload) + 1024)
    try:
        rows = read_csv(find_one(tmp_path, "llm_requests_*.csv"))
    finally:
        csv.field_size_limit(old_limit)
    assert len(rows) == 1
    assert rows[0]["raw_llm_answer"] == payload

    workbook = openpyxl.load_workbook(find_one(tmp_path, "database_export_*.xlsx"))
    sheet_rows = list(workbook["LLM Requests"].iter_rows(values_only=True))
    by_column = dict(zip(sheet_rows[0], sheet_rows[1], strict=True))
    assert isinstance(by_column["raw_llm_answer"], str)


def test_oversized_xlsx_cells_are_cut_with_a_visible_marker(tmp_path):
    answer = "a" * 40_000 + "ANSWER-TAIL"
    error_notes = "e" * 40_000 + "ERROR-TAIL"
    at_the_limit = "b" * EXCEL_MAX_CELL_CHARS

    db_path = tmp_path / "application_state.db"
    seed_database(db_path)
    controller = SQLiteDatabaseController(db_path)
    controller.handle_llm_requests(1, [
        RequestOutcome(1, (1, 2), "ok", answer, error_notes),
        RequestOutcome(2, (3,), "ok", at_the_limit, ""),
    ])
    controller.record_run_configuration(True, 'table_per_file', ("answer",))
    controller.close()

    exporter = SQLiteDataExporter(db_path)
    exporter.export_all_formats(tmp_path)

    workbook = openpyxl.load_workbook(find_one(tmp_path, "database_export_*.xlsx"))
    sheet_rows = list(workbook["LLM Requests"].iter_rows(values_only=True))
    by_column = dict(zip(sheet_rows[0], sheet_rows[1], strict=True))

    for column, full_text in {"raw_llm_answer": answer,
                              "llm_network_error": error_notes}.items():
        cell = by_column[column]
        assert isinstance(cell, str)
        assert cell.startswith(TRUNCATION_MARKER), (
            f"{column}: an oversized cell must announce its truncation")
        assert len(cell) == EXCEL_MAX_CELL_CHARS
        marker_end = cell.index(". ") + 2
        assert "llm_requests_" in cell[:marker_end]
        assert cell[marker_end:] == full_text[:EXCEL_MAX_CELL_CHARS - marker_end]

    boundary_row = dict(zip(sheet_rows[0], sheet_rows[2], strict=True))
    assert boundary_row["raw_llm_answer"] == at_the_limit

    old_limit = csv.field_size_limit(len(answer) + 1024)
    try:
        rows = read_csv(find_one(tmp_path, "llm_requests_*.csv"))
    finally:
        csv.field_size_limit(old_limit)
    assert rows[0]["raw_llm_answer"] == answer



def test_xlsx_round_trips_seeded_content(exported):
    workbook = openpyxl.load_workbook(find_one(exported, "database_export_*.xlsx"))

    assert workbook.sheetnames == ["Summary", "Results", "Details>>>", "Master Registry", "Page Log"]

    master = list(workbook["Master Registry"].iter_rows(values_only=True))
    assert list(master[0]) == REGISTRY_REPORT_COLUMNS
    by_column = [dict(zip(master[0], row, strict=True)) for row in master[1:]]
    assert [r["file_path"] for r in by_column] == [f[1] for f in FILES]
    assert [r["file_id"] for r in by_column] == [f[0] for f in FILES]
    assert [r["total_pages"] for r in by_column] == [f[3] for f in FILES]
    assert [r["overall_result"] for r in by_column] == \
        [EXPECTED_VIEW_CELLS[f[0]][2] for f in FILES]

    pages = list(workbook["Page Log"].iter_rows(values_only=True))
    assert list(pages[0]) == PAGE_LOG_REPORT_COLUMNS
    page_rows = [dict(zip(pages[0], row, strict=True)) for row in pages[1:]]
    assert len(page_rows) == len(PAGES)
    assert [r["output_file"] for r in page_rows] == [p[2] or None for p in PAGES]
    assert [r["page_to_jpeg_status"] for r in page_rows] == [p[3] for p in PAGES]
    assert [r["video_frame_timestamp"] for r in page_rows] == \
        [cell or None for cell in EXPECTED_CAPTURE_CELLS]


def test_xlsx_page_log_cuts_at_row_limit_and_csv_stays_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(data_exporter, "EXCEL_MAX_ROWS", 5)
    db_path = tmp_path / "application_state.db"
    controller = SQLiteDatabaseController(db_path)
    controller.record_run_configuration(False, "", ())
    controller.handle_file_started(1, "bulk/scan.pdf", ".pdf", "TestPipeline")
    for n in range(1, 13):
        controller.handle_frame_saved(1, PageResult(n, f"{n}.jpg", "ok", ""))
    controller.close()
    SQLiteDataExporter(db_path).export_all_formats(tmp_path)
    book = openpyxl.load_workbook(find_one(tmp_path, "database_export_*.xlsx"))
    assert [n for n in book.sheetnames if n.startswith("Page Log")] == ["Page Log"]
    rows = list(book["Page Log"].values)
    assert list(rows[0]) == PAGE_LOG_REPORT_COLUMNS
    assert len(rows) == 6 and [r[2] for r in rows[1:5]] == [1, 2, 3, 4]
    assert all(str(c).startswith("[ROWS OMITTED]") for c in rows[-1])
    assert len(read_csv(find_one(tmp_path, "page_log_*.csv"))) == 12



def test_detail_reports_share_explicit_file_first_order_and_keep_id_links(tmp_path):
    from schemas import AnswerRow

    db = tmp_path / "state.db"
    seed_database(db)
    controller = SQLiteDatabaseController(db)
    try:
        controller.record_run_configuration(True, "table_per_page", ("answer",))
        for file_id, text in ((1, "first"), (3, "third")):
            controller.handle_llm_requests(file_id, [RequestOutcome(
                request_number=1, pages=(1,), status="ok", raw_answer=text, error="",
                answer_rows=(AnswerRow(1, "1", "", json.dumps({"answer": text})),),
            )])
    finally:
        controller.close()
    exporter = SQLiteDataExporter(db)
    exporter.export_all_formats(tmp_path / "out")
    book = openpyxl.load_workbook(find_one(tmp_path / "out", "*.xlsx"))
    expected = {
        "Page Log": ("page_log", ["file_id", "page_id", "page_number", "output_file",
                                  "page_to_jpeg_status", "page_to_jpeg_comment", "video_frame_timestamp"]),
        "LLM Requests": ("llm_requests", ["file_id", "request_id", "request_number", "pages",
                                          "request_status", "raw_llm_answer", "llm_network_error"]),
        "LLM Answers": ("llm_answers", ["file_id", "page_id", "request_id", "llm_answer_id",
                                        "raw_model_page_number", "llm_error", "llm_answer"]),
    }
    try:
        for title, (stem, headers) in expected.items():
            with find_one(tmp_path / "out", stem + "_*.csv").open(encoding="utf-8", newline="") as stream:
                csv_rows = list(csv.reader(stream))
            xlsx_rows = [["" if value is None else str(value) for value in row]
                         for row in book[title].values]
            assert csv_rows[0] == headers, title
            assert xlsx_rows == csv_rows, title
        assert list(book["LLM Answers"].values)[2][:4] == (3, 5, 2, 2)
        assert list(book["LLM Requests"].values)[2][:3] == (3, 2, 1)
        assert list(book["Page Log"].values)[5][:3] == (3, 5, 1)
    finally:
        book.close()
