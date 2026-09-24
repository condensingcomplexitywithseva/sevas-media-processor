# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import json
import logging
import sqlite3
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import data_exporter
from config_validator import output_columns_error
from data_exporter import LLM_ANSWERS_REPORT_COLUMNS, LLM_REQUESTS_REPORT_COLUMNS
from db_controller import SQLiteDatabaseController
from schemas import AnswerRow, RequestOutcome, FileSummary, PageResult

from test_llm_answer_exports import (
    DECLARED,
    RESULTS_SHEET_TITLE,
    export_csvs,
    export_xlsx,
    read_csv,
    seed_db,
    sheet_rows,
)
from test_llm_answer_storage import add_file, answer_row, request


@pytest.fixture
def db(tmp_path):
    controller = SQLiteDatabaseController(tmp_path / "state.db")
    yield controller
    controller.close()


def index_of(rows):
    return dict(zip(rows[0], range(len(rows[0])), strict=True))


def sql(db_path, statement, *params):
    connection = sqlite3.connect(db_path)
    try:
        cursor = connection.execute(statement, params)
        rows = cursor.fetchall()
        connection.commit()
        return rows
    finally:
        connection.close()




def test_request_row_and_answer_rows_are_one_transaction(db, tmp_path):
    add_file(db, 1, "img.png", page_count=1)
    sql(
        tmp_path / "state.db",
        "CREATE TRIGGER refuse_answers BEFORE INSERT ON llm_answers BEGIN SELECT RAISE(ABORT, 'disk full'); END",
    )

    with pytest.raises(SQLAlchemyError):
        db.handle_llm_requests(
            1,
            [
                request(
                    end=1, rows=[answer_row(page_number=1, raw_model_page_number="1", values_json='{"answer": "a"}')]
                )
            ],
        )

    assert sql(tmp_path / "state.db", "SELECT COUNT(*) FROM llm_requests")[0][0] == 0


def test_answer_row_errors_keep_the_file_out_of_the_resume_skip(db, tmp_path):
    add_file(db, 1, "misnumbered.png", page_count=1)
    db.handle_llm_requests(
        1,
        [
            request(
                end=1,
                rows=[
                    answer_row(
                        page_number=None,
                        raw_model_page_number="9",
                        llm_error="hallucinated_page_number",
                        values_json='{"answer": "a"}',
                    ),
                    answer_row(page_number=1, llm_error="no_row_returned"),
                ],
            )
        ],
    )
    add_file(db, 2, "clean.png", page_count=1)
    db.handle_llm_requests(
        2, [request(end=1, rows=[answer_row(page_number=1, raw_model_page_number="1", values_json='{"answer": "a"}')])]
    )

    assert set(db.get_no_retry_sources(["ok"])) == {"clean.png"}




def test_json_scalars_keep_their_json_spelling_in_cells(tmp_path):
    db_path = seed_db(
        tmp_path,
        [
            (1, "", json.dumps({"genre": True, "answer": 10**30})),
            (2, "", json.dumps({"genre": False, "answer": 100.0})),
        ],
    )
    rows = read_csv(export_csvs(db_path, tmp_path / "csv"))
    assert [r["llm_genre"] for r in rows] == ["true", "false"]
    assert [r["llm_answer"] for r in rows] == [str(10**30), "100.0"]

    workbook = export_xlsx(db_path, tmp_path / "xlsx")
    sheet = sheet_rows(workbook, "LLM Answers")
    index = index_of(sheet)
    assert [str(r[index["llm_genre"]]) for r in sheet[1:]] == ["true", "false"]
    assert [str(r[index["llm_answer"]]) for r in sheet[1:]] == [str(10**30), "100.0"]


def test_null_values_export_as_empty_cells(tmp_path):
    db_path = seed_db(
        tmp_path,
        [
            (1, "", json.dumps({"genre": None, "answer": "a"})),
        ],
    )
    row = read_csv(export_csvs(db_path, tmp_path / "csv"))[0]
    assert row["llm_genre"] == ""

    workbook = export_xlsx(db_path, tmp_path / "xlsx")
    for title in ("LLM Answers", RESULTS_SHEET_TITLE):
        sheet = sheet_rows(workbook, title)
        assert sheet[1][index_of(sheet)["llm_genre"]] in ("", None), title


def test_nested_non_ascii_values_stay_readable_in_cells(tmp_path):
    city = "Москва"
    db_path = seed_db(
        tmp_path,
        [
            (1, "", json.dumps({"genre": {"city": city}, "answer": "a"}, ensure_ascii=False)),
        ],
    )
    row = read_csv(export_csvs(db_path, tmp_path / "csv"))[0]
    assert city in row["llm_genre"]
    assert "\\u04" not in row["llm_genre"]
    assert json.loads(row["llm_genre"]) == {"city": city}




def build_mixed_run(tmp_path):
    db_path = tmp_path / "state.db"
    controller = SQLiteDatabaseController(db_path)

    def file_with_pages(file_id, name, statuses):
        controller.handle_file_started(file_id, name, Path(name).suffix, "Stub")
        for number, status in enumerate(statuses, start=1):
            controller.handle_frame_saved(
                file_id, PageResult(number, f"{file_id}_page_{number}.jpg" if status == "ok" else "", status, "")
            )
        controller.handle_file_completed(file_id, FileSummary(len(statuses), f"1-{len(statuses)}", "ok", "done"))
        controller.finalize_file(file_id)

    file_with_pages(1, "answered.png", ["ok", "ok"])
    controller.handle_llm_requests(
        1,
        [
            RequestOutcome(
                1,
                (1, 2),
                "ok",
                "[...]",
                "",
                answer_rows=(
                    AnswerRow(1, "1", "", '{"genre": "g1", "answer": "a1"}'),
                    AnswerRow(2, "2", "", '{"genre": "g2", "answer": "a2"}'),
                ),
            )
        ],
    )
    file_with_pages(2, "skipped_by_ai.png", ["ok", "ok"])
    file_with_pages(3, "all_failed.pdf", ["ok", "ok"])
    controller.handle_llm_requests(3, [RequestOutcome(1, (1, 2), "invalid_json_answer", "garbage", "")])
    file_with_pages(4, "torn.png", ["failure"])
    controller.record_run_configuration(True, "table_per_page", DECLARED)
    controller.close()
    return db_path


def test_reader_never_loses_a_request_or_file(tmp_path):
    db = build_mixed_run(tmp_path)
    rows = sheet_rows(export_xlsx(db, tmp_path / "out"), "Results")
    assert [(r[1], r[2], r[4]) for r in rows[1:]] == [
        ("answered.png", 1, "g1"),
        ("answered.png", 2, "g2"),
        ("skipped_by_ai.png", None, None),
        ("all_failed.pdf", "1-2", None),
        ("torn.png", None, None),
    ]
    assert "AI completion cannot be confirmed" in rows[3][-1]
    assert "Answer format invalid" in rows[4][-1]
    assert "failed conversion" in rows[5][-1]


def test_results_explains_a_failed_conversion_beside_the_answers(tmp_path):
    db_path = tmp_path / "state.db"
    controller = SQLiteDatabaseController(db_path)
    controller.handle_file_started(1, "scan.pdf", ".pdf", "Stub")
    controller.handle_frame_saved(1, PageResult(1, "1_page_1.jpg", "ok", ""))
    controller.handle_frame_saved(1, PageResult(2, "", "failure", "decoder choked"))
    controller.handle_frame_saved(1, PageResult(3, "1_page_3.jpg", "ok", ""))
    controller.handle_frame_saved(1, PageResult(4, "", "skipped", "Scene static"))
    controller.handle_file_completed(1, FileSummary(4, "1-4", "ok", "done"))
    controller.finalize_file(1)
    controller.handle_llm_requests(
        1,
        [
            RequestOutcome(
                1,
                (1, 2, 3),
                "ok",
                "[...]",
                "",
                answer_rows=(
                    AnswerRow(1, "1", "", '{"genre": "g1", "answer": "a1"}'),
                    AnswerRow(3, "3", "", '{"genre": "g3", "answer": "a3"}'),
                ),
            )
        ],
    )
    controller.handle_file_started(2, "never_sent.png", ".png", "Stub")
    controller.handle_frame_saved(2, PageResult(1, "", "failure", "unreadable"))
    controller.handle_file_completed(2, FileSummary(1, "1", "ok", "done"))
    controller.finalize_file(2)
    controller.record_run_configuration(True, "table_per_page", DECLARED)
    controller.close()

    workbook = export_xlsx(db_path, tmp_path / "out")
    sheet = sheet_rows(workbook, RESULTS_SHEET_TITLE)
    assert [(r[1], r[2], r[4]) for r in sheet[1:]] == [
        ("scan.pdf", 1, "g1"),
        ("scan.pdf", 3, "g3"),
        ("never_sent.png", None, None),
    ]
    assert all("failed conversion" in r[-1] for r in sheet[1:])


def test_an_ai_off_workbook_carries_no_llm_columns(tmp_path):
    db_path = tmp_path / "state.db"
    controller = SQLiteDatabaseController(db_path)
    controller.handle_file_started(1, "img.png", ".png", "Stub")
    controller.handle_frame_saved(1, PageResult(1, "1_page_1.jpg", "ok", ""))
    controller.handle_file_completed(1, FileSummary(1, "1", "ok", "done"))
    controller.finalize_file(1)
    controller.record_run_configuration(False, "", ())
    controller.close()

    workbook = export_xlsx(db_path, tmp_path / "out")
    assert "LLM Answers" not in workbook.sheetnames
    headers = sheet_rows(workbook, RESULTS_SHEET_TITLE)[0]
    assert headers == RESULTS_REPORT_COLUMNS, headers




def test_hostile_page_claims_ride_the_xlsx_gates(tmp_path):
    hostile = "claim \x07 with \x00 junk " + "9" * 40_000
    db_path = seed_db(
        tmp_path,
        [
            (None, "hallucinated_page_number", json.dumps({"genre": "g", "answer": "a"})),
        ],
    )
    sql(db_path, "UPDATE llm_answers SET raw_model_page_number = ?", hostile)

    workbook = export_xlsx(db_path, tmp_path / "xlsx")
    sheet = sheet_rows(workbook, "LLM Answers")
    cell = sheet[1][index_of(sheet)["raw_model_page_number"]]
    assert "\x07" not in cell and "\x00" not in cell
    assert "�" in cell
    assert len(cell) <= 32_767
    assert cell.startswith("[TEXT SHORTENED] ")

    answers_csv = export_csvs(db_path, tmp_path / "csv")
    assert hostile in answers_csv.read_text(encoding="utf-8")


FORMULA_SHAPED = [
    '=HYPERLINK("http://evil.example","click")',
    "=1+1",
    "+1+1",
    "-2",
    "@SUM(A1)",
    "=cmd|' /C calc'!A0",
]


def test_no_cell_becomes_a_live_formula(tmp_path):
    db_path = seed_db(tmp_path, [(1, "", json.dumps({"genre": shape, "answer": shape})) for shape in FORMULA_SHAPED])
    sql(db_path, "UPDATE llm_answers SET raw_model_page_number = ?", FORMULA_SHAPED[0])
    sql(db_path, "UPDATE llm_requests SET raw_llm_answer = ?", FORMULA_SHAPED[0])
    sql(db_path, "UPDATE file_registry SET file_path = ?", "=cmd().png")

    workbook = export_xlsx(db_path, tmp_path / "out")
    live = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value is not None and cell.data_type == "f":
                    live.append((sheet.title, cell.coordinate, cell.value))
    assert not live, f"live formulas in the workbook: {live}"
    sheet = sheet_rows(workbook, "LLM Answers")
    index = index_of(sheet)
    assert [r[index["llm_genre"]] for r in sheet[1:]] == FORMULA_SHAPED




def test_no_spread_report_header_can_collide_with_an_accepted_key():
    for headers in (LLM_ANSWERS_REPORT_COLUMNS, RESULTS_REPORT_COLUMNS):
        for header in headers:
            if header.startswith("llm_"):
                assert output_columns_error(header[len("llm_") :]) == "err_output_columns_reserved", header
    assert "llm_network_error" in LLM_REQUESTS_REPORT_COLUMNS


def build_twelve_request_run(tmp_path):
    db_path = tmp_path / "state.db"
    controller = SQLiteDatabaseController(db_path)
    controller.handle_file_started(1, "long.pdf", ".pdf", "Stub")
    for number in range(1, 13):
        controller.handle_frame_saved(1, PageResult(number, f"1_page_{number}.jpg", "ok", ""))
    controller.handle_file_completed(1, FileSummary(12, "1-12", "ok", "done"))
    controller.finalize_file(1)
    controller.handle_llm_requests(
        1,
        [
            RequestOutcome(
                number,
                (number,),
                "ok",
                f"reply {number}",
                "",
                answer_rows=(AnswerRow(number, str(number), "", json.dumps({"genre": "g", "answer": "a"})),),
            )
            for number in range(1, 13)
        ],
    )
    controller.record_run_configuration(True, "table_per_page", DECLARED)
    controller.close()
    return db_path


@pytest.mark.parametrize("title", ["LLM Requests", "LLM Answers", RESULTS_SHEET_TITLE])
def test_every_sheet_respects_the_row_limit(tmp_path, monkeypatch, title):
    monkeypatch.setattr(data_exporter, "EXCEL_MAX_ROWS", 5)
    db_path = build_twelve_request_run(tmp_path)
    workbook = export_xlsx(db_path, tmp_path / "out")

    parts = [name for name in workbook.sheetnames if name.startswith(title)]
    assert parts, workbook.sheetnames
    data_rows = 0
    for name in parts:
        rows = sheet_rows(workbook, name)
        assert len(rows) - 1 <= 5, (name, len(rows) - 1)
        data_rows += len(rows) - 1
    assert data_rows == 5
    assert all(str(c).startswith("[ROWS OMITTED]") for c in sheet_rows(workbook, title)[-1])
    stem = {"Results": "results", "LLM Requests": "llm_requests", "LLM Answers": "llm_answers"}[title]
    assert len(read_csv(next((tmp_path / "out").glob(stem + "_*.csv")))) == 12


def wide_db(tmp_path, keys):
    values = {key: f"v{i}" for i, key in enumerate(keys)}
    return seed_db(tmp_path, [(1, "", json.dumps(values))], declared=keys)


def test_three_hundred_columns_export_end_to_end(tmp_path):
    keys = [f"c{i}" for i in range(300)]
    db_path = wide_db(tmp_path, keys)
    answers_csv = export_csvs(db_path, tmp_path / "csv")
    row = read_csv(answers_csv)[0]
    assert [row[f"llm_{k}"] for k in keys] == [f"v{i}" for i in range(300)]

    workbook = export_xlsx(db_path, tmp_path / "xlsx")
    for title in ("LLM Answers", RESULTS_SHEET_TITLE):
        sheet = sheet_rows(workbook, title)
        assert [h for h in sheet[0] if h.startswith("llm_") and h not in LLM_ANSWERS_REPORT_COLUMNS] == [
            f"llm_{k}" for k in keys
        ], title


def test_seventeen_thousand_columns_survive_with_a_complete_csv(tmp_path, caplog):
    keys = [f"k{i}" for i in range(17_000)]
    db_path = wide_db(tmp_path, keys)
    answers_csv = export_csvs(db_path, tmp_path / "csv")
    with open(answers_csv, encoding="utf-8", newline="") as handle:
        header = handle.readline().rstrip("\r\n").split(",")
    assert len(header) == len(LLM_ANSWERS_REPORT_COLUMNS) + 17_000

    with caplog.at_level(logging.WARNING):
        workbook = export_xlsx(db_path, tmp_path / "xlsx")
    too_wide = [(s.title, s.max_column) for s in workbook.worksheets if s.max_column > 16_384]
    assert not too_wide, f"sheets Excel cannot open: {too_wide}"
    assert any("columns omitted" in record.getMessage() for record in caplog.records), (
        "no warning pointed the user at the CSV"
    )


RESULTS_REPORT_COLUMNS = ["file_id", "file_path", "file_result", "notes"]
