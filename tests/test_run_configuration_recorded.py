# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import central_logger
import data_exporter
from db_controller import SQLiteDatabaseController

from fake_llm import make_server

from test_llm_run_cruelty import point_tokens_at_the_fake, run_core, settings_for
from test_results_report import (
    RESULTS_STEM, answer_row, read_csv_rows, request_outcome,
    results_sheet, seed_one_file, sheet_rows,
)
from test_workbook_limits import limits_lines, section, summary_table, workbook_in



def record(controller, ai_enabled=True, output_mode="table_per_file",
           declared_columns=("answer",)) -> None:
    recorder = getattr(controller, "record_run_configuration", None)
    assert recorder is not None, (
        "SQLiteDatabaseController.record_run_configuration must exist (the run's recorded configuration)")
    recorder(ai_enabled=ai_enabled, output_mode=output_mode,
             declared_columns=tuple(declared_columns))


def exporter_under_live_settings(db_path, live_columns):
    return data_exporter.SQLiteDataExporter(db_path)


def export_under_live_settings(db_path, out_dir, live_columns, workbook=False):
    out_dir.mkdir(parents=True, exist_ok=True)
    exporter = exporter_under_live_settings(db_path, live_columns)
    if workbook:
        exporter.export_all_formats(out_dir)
    else:
        exporter.export_csv(out_dir, timestamp="t")
    return out_dir


def seed_answered_file(db_path, values):
    seed_one_file(db_path, [(1, "1_p1.jpg", "ok", "", None)],
                  requests=[request_outcome(pages=(1,), answer_rows=(answer_row(values=values),))])
    controller = SQLiteDatabaseController(db_path)
    try:
        record(controller, declared_columns=("answer",))
    finally:
        controller.close()
    return db_path



def test_a_columns_edit_after_the_run_changes_nothing_in_the_report(tmp_path):
    db_path = seed_answered_file(tmp_path / "state.db", {"answer": "a"})
    out = export_under_live_settings(db_path, tmp_path / "out", ("answer", "total"))
    headers, rows = read_csv_rows(out / f"{RESULTS_STEM}_t.csv")
    assert headers == ["file_id", "file_path", "pages", "file_result", "llm_answer", "notes"]
    assert rows[0]["llm_answer"] == "a"


def test_ai_switched_off_after_the_run_still_exports_the_ai_report(tmp_path):
    db_path = seed_answered_file(tmp_path / "state.db", {"answer": "a"})
    out = export_under_live_settings(db_path, tmp_path / "out", (), workbook=True)
    assert len(list(out.glob("llm_requests_*.csv"))) == 1
    answers_csv = next(out.glob("llm_answers_*.csv"))
    answers_headers, _ = read_csv_rows(answers_csv)
    assert answers_headers[-1] == "llm_answer"
    workbook = workbook_in(out)
    assert "LLM Requests" in workbook.sheetnames and "LLM Answers" in workbook.sheetnames
    results_headers, _ = results_sheet(workbook)
    assert results_headers == ["file_id", "file_path", "pages", "file_result", "llm_answer", "notes"]


def test_an_ai_run_that_reached_no_request_never_reads_as_ai_off(tmp_path):
    db_path = tmp_path / "state.db"
    seed_one_file(db_path, [(1, "", "failure", "cannot identify image file", None)],
                  total=0, page_range="", range_status="failure",
                  comment="cannot identify image file")
    controller = SQLiteDatabaseController(db_path)
    try:
        record(controller, ai_enabled=True, declared_columns=("answer",))
    finally:
        controller.close()
    out = export_under_live_settings(db_path, tmp_path / "out", ("answer",), workbook=True)
    workbook = workbook_in(out)
    table = summary_table(workbook)
    ai_off_line = getattr(data_exporter, "SUMMARY_AI_OFF_LINE", "AI processing was off for this run")
    assert ai_off_line not in [item for _, item, _ in table]
    assert section(table, "ai") == {}
    assert section(table, "requests") != {}, "an AI run has a requests section, zeros included"
    assert set(section(table, "requests").values()) == {0}
    headers, _ = results_sheet(workbook)
    assert headers == ["file_id", "file_path", "pages", "file_result", "llm_answer", "notes"]
    assert limits_lines(table), "the limits block is present as on every run"


def test_two_exports_under_two_settings_files_are_identical(tmp_path):
    db_path = seed_answered_file(tmp_path / "state.db", {"answer": "a"})
    first = export_under_live_settings(db_path, tmp_path / "first", ("answer",), workbook=True)
    second = export_under_live_settings(db_path, tmp_path / "second", ("genre", "year"), workbook=True)
    for csv_path in sorted(first.glob("*.csv")):
        stem = csv_path.name.rsplit("_", 2)[0]
        twin = next(second.glob(f"{stem}_*.csv"))
        assert csv_path.read_bytes() == twin.read_bytes(), stem
    first_book, second_book = workbook_in(first), workbook_in(second)
    assert first_book.sheetnames == second_book.sheetnames
    def facts(book, title, directory):
        rows = sheet_rows(book, title)
        if title == "Summary":
            for row in rows:
                if len(row) > 1 and isinstance(row[1], str):
                    for csv_path in directory.glob("*.csv"):
                        stem = csv_path.name.rsplit("_", 2)[0]
                        row[1] = row[1].replace(csv_path.name, stem + ".csv")
        return rows
    for title in first_book.sheetnames:
        assert facts(first_book, title, first) == facts(second_book, title, second), title


def test_a_stored_value_under_an_undeclared_key_is_never_dropped(tmp_path):
    db_path = seed_answered_file(tmp_path / "state.db", {"answer": "a", "extra": "b"})
    out_dir = tmp_path / "out"
    try:
        out = export_under_live_settings(db_path, out_dir, ("answer",))
    except Exception:
        return
    headers, rows = read_csv_rows(out / f"{RESULTS_STEM}_t.csv")
    assert "llm_extra" in headers, headers
    assert rows[0]["llm_extra"] == "b"


def test_a_database_without_a_record_is_refused_by_the_export(tmp_path):
    from schemas import ConfigurationError
    db_path = tmp_path / "state.db"
    controller = SQLiteDatabaseController(db_path)
    try:
        controller.handle_file_started(1, "photo.png", ".png", "Stub")
    finally:
        controller.close()
    exporter = exporter_under_live_settings(db_path, ("answer",))
    with pytest.raises(ConfigurationError) as refused:
        exporter.export_all_formats(tmp_path / "out")
    assert "state.db" in str(refused.value)



@pytest.mark.parametrize("ai_on", [True, False], ids=["ai_on", "ai_off"])
def test_the_configuration_is_recorded_even_when_no_file_was_processed(tmp_path, monkeypatch, ai_on):
    monkeypatch.setattr(central_logger, "setup_logging", lambda *a, **k: None)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    point_tokens_at_the_fake(monkeypatch, "openai")
    server = make_server("openai").start()
    try:
        settings = settings_for((input_dir, tmp_path / "output"), "openai", server.base_url,
                                ENABLE_LLM_INFERENCE=ai_on, LLM_OUTPUT_COLUMNS="answer, genre")
        events, db_path = run_core(settings)
    finally:
        server.stop()
    assert events[-1] == {"type": "done"}, events[-3:]
    controller = SQLiteDatabaseController(db_path)
    try:
        reader = getattr(controller, "get_run_configuration", None)
        assert reader is not None, (
            "SQLiteDatabaseController.get_run_configuration must exist (the run's recorded configuration)")
        recorded = reader()
    finally:
        controller.close()
    assert recorded is not None, "the record is written before the first file"
    assert recorded.ai_enabled is ai_on
    assert recorded.output_mode == ("table_per_file" if ai_on else "")
    assert tuple(recorded.declared_columns) == (("answer", "genre") if ai_on else ())
