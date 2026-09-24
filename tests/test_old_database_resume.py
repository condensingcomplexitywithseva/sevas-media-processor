# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import ast
import json
import logging
import sqlite3
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import central_logger
from schemas import ConfigurationError
from config_validator import tech_folder_path

from fake_llm import make_server

from test_llm_run_cruelty import make_png, point_tokens_at_the_fake, run_core, settings_for

OLD_SCHEMA = """
CREATE TABLE file_registry (
    file_id INTEGER NOT NULL, file_path VARCHAR NOT NULL, file_ext VARCHAR NOT NULL,
    total_pages INTEGER NOT NULL, file_to_jpegs_comment VARCHAR NOT NULL,
    page_range VARCHAR NOT NULL, range_status VARCHAR NOT NULL,
    PRIMARY KEY (file_id));
CREATE INDEX ix_file_registry_file_path ON file_registry (file_path);
CREATE TABLE page_log (
    page_id INTEGER NOT NULL, file_id INTEGER NOT NULL, page_number INTEGER NOT NULL,
    output_file VARCHAR NOT NULL, page_to_jpeg_status VARCHAR NOT NULL,
    page_to_jpeg_comment VARCHAR NOT NULL, video_frame_timestamp VARCHAR NOT NULL,
    PRIMARY KEY (page_id), FOREIGN KEY(file_id) REFERENCES file_registry (file_id));
CREATE INDEX ix_page_log_file_id ON page_log (file_id);
CREATE TABLE llm_requests (
    chunk_id INTEGER NOT NULL, file_id INTEGER NOT NULL, chunk_number INTEGER NOT NULL,
    chunk_start INTEGER NOT NULL, chunk_end INTEGER NOT NULL, chunk_status VARCHAR NOT NULL,
    raw_llm_answer VARCHAR NOT NULL, llm_network_error VARCHAR NOT NULL,
    PRIMARY KEY (chunk_id), FOREIGN KEY(file_id) REFERENCES file_registry (file_id));
CREATE INDEX ix_llm_requests_file_id ON llm_requests (file_id);
CREATE TABLE llm_answers (
    llm_answer_id INTEGER NOT NULL, chunk_id INTEGER NOT NULL, file_id INTEGER NOT NULL,
    page_id INTEGER, raw_model_page_number VARCHAR NOT NULL, llm_error VARCHAR NOT NULL,
    values_json VARCHAR NOT NULL, PRIMARY KEY (llm_answer_id),
    FOREIGN KEY(chunk_id) REFERENCES llm_requests (chunk_id),
    FOREIGN KEY(file_id) REFERENCES file_registry (file_id),
    FOREIGN KEY(page_id) REFERENCES page_log (page_id));
CREATE INDEX ix_llm_answers_chunk_id ON llm_answers (chunk_id);
CREATE INDEX ix_llm_answers_file_id ON llm_answers (file_id);
"""

V010_SCHEMA = """
CREATE TABLE databasefileregistry (
    unique_file_id INTEGER NOT NULL, relative_file_path VARCHAR NOT NULL,
    original_extension VARCHAR NOT NULL, total_discovered_pages INTEGER NOT NULL,
    final_aggregate_status VARCHAR NOT NULL, final_aggregate_comment VARCHAR NOT NULL,
    applied_range_string VARCHAR NOT NULL, range_status_code VARCHAR NOT NULL,
    llm_network_answer VARCHAR, llm_network_error VARCHAR, llm_answer_json VARCHAR NOT NULL,
    PRIMARY KEY (unique_file_id));
CREATE INDEX ix_databasefileregistry_relative_file_path ON databasefileregistry (relative_file_path);
CREATE TABLE databasepagelog (
    primary_database_id INTEGER NOT NULL, parent_file_id INTEGER NOT NULL,
    page_or_frame_number INTEGER NOT NULL, saved_filename VARCHAR NOT NULL,
    execution_status VARCHAR NOT NULL, execution_comment VARCHAR NOT NULL,
    capture_timestamp VARCHAR NOT NULL, llm_answer_json VARCHAR NOT NULL,
    PRIMARY KEY (primary_database_id),
    FOREIGN KEY(parent_file_id) REFERENCES databasefileregistry (unique_file_id));
CREATE INDEX ix_databasepagelog_parent_file_id ON databasepagelog (parent_file_id);
"""

OLDER_POPULATED = {
    "pre-pages": (OLD_SCHEMA, "INSERT INTO file_registry VALUES (1,'photo.png','.png',1,'done','1','ok')"),
    "v0.1.0": (V010_SCHEMA, "INSERT INTO databasefileregistry "
                            "VALUES (1,'photo.png','.png',1,'ok','done','1','ok',NULL,NULL,'')"),
}


@pytest.fixture(params=sorted(OLDER_POPULATED))
def old_database_folder(request, tmp_path, monkeypatch):
    monkeypatch.setattr(central_logger, "setup_logging", lambda *a, **k: None)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_png(input_dir / "photo.png")
    output_dir = tmp_path / "output"
    tech = tech_folder_path(str(output_dir))
    tech.mkdir(parents=True)
    schema, saved_file = OLDER_POPULATED[request.param]
    connection = sqlite3.connect(tech / "application_state.db")
    try:
        connection.executescript(schema)
        connection.execute(saved_file)
        connection.commit()
    finally:
        connection.close()
    return input_dir, output_dir


def test_the_old_schema_is_really_old():
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(OLD_SCHEMA)
        requests = [row[1] for row in connection.execute("PRAGMA table_info(llm_requests)")]
        answers = [row[1] for row in connection.execute("PRAGMA table_info(llm_answers)")]
    finally:
        connection.close()
    assert requests == ["chunk_id", "file_id", "chunk_number", "chunk_start", "chunk_end",
                        "chunk_status", "raw_llm_answer", "llm_network_error"]
    assert "chunk_id" in answers and "request_id" not in answers


def test_the_v010_schema_is_really_the_release_layout():
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(V010_SCHEMA)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert tables == {"databasefileregistry", "databasepagelog"}


def test_resuming_into_an_old_database_is_refused_before_any_upload(
        old_database_folder, monkeypatch, caplog):
    input_dir, output_dir = old_database_folder
    database = tech_folder_path(str(output_dir)) / "application_state.db"
    with sqlite3.connect(database) as connection:
        before = list(connection.iterdump())
    connection.close()
    point_tokens_at_the_fake(monkeypatch, "openai")
    server = make_server("openai", answer='{"answer": "paid"}').start()
    try:
        settings = settings_for((input_dir, output_dir), "openai", server.base_url,
                                LLM_OUTPUT_COLUMNS="answer", START_OVER=False)
        with caplog.at_level(logging.ERROR):
            try:
                events, _ = run_core(settings)
                messages = [r.getMessage() for r in caplog.records]
            except Exception as refused:
                events = [{"type": "refused"}]
                messages = [str(refused)]
        uploads = len(server.requests)
    finally:
        server.stop()

    assert uploads == 0, "the old tables are refused before any image is uploaded"
    assert events[-1]["type"] != "done", "an old database must never read as a clean run"
    refusal = [m for m in messages if "application_state.db" in m]
    assert refusal, f"the refusal must name the database file; got {messages}"
    with sqlite3.connect(database) as connection:
        assert list(connection.iterdump()) == before, "the refusal writes nothing"
    connection.close()


def test_start_over_against_the_same_folder_archives_it_and_runs_clean(
        old_database_folder, monkeypatch):
    input_dir, output_dir = old_database_folder
    point_tokens_at_the_fake(monkeypatch, "openai")
    server = make_server("openai", answer='{"answer": "fresh"}').start()
    try:
        settings = settings_for((input_dir, output_dir), "openai", server.base_url,
                                LLM_OUTPUT_COLUMNS="answer", START_OVER=True)
        events, db_path = run_core(settings)
        assert len(server.requests) == 1
    finally:
        server.stop()
    assert events[-1] == {"type": "done"}, events[-3:]
    archived = [p for p in output_dir.iterdir() if p.name.startswith("old_")]
    assert len(archived) == 1, "the previous run folder is archived, never deleted"
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT pages, request_status FROM llm_requests").fetchall() == [
            ("1", "ok")]
    finally:
        connection.close()



def test_the_refusal_names_the_checkbox_as_each_locale_labels_it(old_database_folder):
    from schemas import ConfigurationError
    input_dir, output_dir = old_database_folder
    settings = settings_for((input_dir, output_dir), "openai", "http://127.0.0.1:9",
                            ENABLE_LLM_INFERENCE=False, START_OVER=False)
    with pytest.raises(ConfigurationError) as refused:
        run_core(settings)
    message = str(refused.value)
    assert message.startswith("i18n:"), message
    key, _, detail = message[5:].partition("|")
    assert "application_state.db" in detail, message
    assert refused.value.path == detail, "a path is explicitly typed, never inferred from error prose"
    locales = sorted((SRC / "locales").glob("*.json"))
    assert locales
    for locale_path in locales:
        strings = json.loads(locale_path.read_text(encoding="utf-8"))
        assert key in strings, (locale_path.name, key)
        label = strings["lbl_start_over"].rstrip(":").strip()
        assert "{setting}" in strings[key], (locale_path.name, strings[key])
        assert label not in strings[key], "the renderer supplies the live control label"


CONSTRUCTION_TIME_RAISE_SITES = {
    "app_context.py": 2,
    "config_loader.py": 3,
    "range_parsers.py": 2,
    "db_controller.py": 3,
}


def _configuration_error_messages(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and isinstance(node.exc.func, ast.Name)
                and node.exc.func.id == "ConfigurationError" and node.exc.args):
            continue
        first = node.exc.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append(first.value)
        elif (isinstance(first, ast.JoinedStr) and first.values
              and isinstance(first.values[0], ast.Constant)):
            found.append(str(first.values[0].value))
        else:
            found.append(f"<line {node.lineno}: a message that does not start with literal text>")
    return found


@pytest.mark.parametrize("file_name, sites", sorted(CONSTRUCTION_TIME_RAISE_SITES.items()))
def test_every_construction_time_configuration_error_is_translatable(file_name, sites):
    messages = _configuration_error_messages((SRC / file_name).read_text(encoding="utf-8"))
    assert len(messages) == sites, (
        f"{file_name} has {len(messages)} ConfigurationError raise sites, expected {sites}: "
        "update the table when a site moves")
    locales = [json.loads(p.read_text(encoding="utf-8"))
               for p in sorted((SRC / "locales").glob("*.json"))]
    assert locales
    for message in messages:
        assert message.startswith("i18n:"), message
        key = message[5:].split("|", 1)[0]
        assert key.startswith("err_") and all(key in strings for strings in locales), key



def _sandbox_with_one_photo(tmp_path, monkeypatch):
    monkeypatch.setattr(central_logger, "setup_logging", lambda *a, **k: None)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_png(input_dir / "photo.png")
    return input_dir, tmp_path / "output"


@pytest.mark.parametrize("change", [
    {"LLM_OUTPUT_COLUMNS": "answer, total"},
    {"LLM_OUTPUT_MODE": "table_per_page"},
    {"ENABLE_LLM_INFERENCE": False},
], ids=["columns", "mode", "ai_off"])
def test_a_resume_under_changed_settings_is_refused_before_any_upload(
        tmp_path, monkeypatch, change):
    from schemas import ConfigurationError
    input_dir, output_dir = _sandbox_with_one_photo(tmp_path, monkeypatch)
    point_tokens_at_the_fake(monkeypatch, "openai")
    server = make_server("openai", answer='{"answer": "first"}').start()
    try:
        first = settings_for((input_dir, output_dir), "openai", server.base_url,
                             LLM_OUTPUT_COLUMNS="answer", START_OVER=False)
        events, _ = run_core(first)
        assert events[-1] == {"type": "done"}, events[-3:]
        uploads = len(server.requests)
        changed = settings_for((input_dir, output_dir), "openai", server.base_url,
                               **{"LLM_OUTPUT_COLUMNS": "answer", "START_OVER": False, **change})
        with pytest.raises(ConfigurationError) as refused:
            run_core(changed)
        assert len(server.requests) == uploads, "refused before any upload"
    finally:
        server.stop()
    message = str(refused.value)
    assert message.startswith("i18n:err_resume_configuration_changed|"), message
    assert refused.value.setting_field == "START_OVER"
    detail = message.partition("|")[2]
    assert "->" in detail and detail, "the detail names what changed"


def test_a_resume_under_the_same_settings_continues_and_skips_the_done_file(
        tmp_path, monkeypatch):
    from db_controller import SQLiteDatabaseController
    input_dir, output_dir = _sandbox_with_one_photo(tmp_path, monkeypatch)
    point_tokens_at_the_fake(monkeypatch, "openai")
    server = make_server("openai", answer='{"answer": "first"}').start()
    try:
        settings = settings_for((input_dir, output_dir), "openai", server.base_url,
                                LLM_OUTPUT_COLUMNS="answer", START_OVER=False)
        run_core(settings)
        events, db_path = run_core(settings)
        assert len(server.requests) == 1, "the done file is resume-skipped"
    finally:
        server.stop()
    assert events[-1] == {"type": "done"}, events[-3:]
    controller = SQLiteDatabaseController(db_path)
    try:
        recorded = controller.get_run_configuration()
    finally:
        controller.close()
    assert recorded is not None
    assert (recorded.ai_enabled, recorded.output_mode, tuple(recorded.declared_columns)) == (
        True, "table_per_file", ("answer",))


@pytest.mark.skipif(sys.platform != "win32",
                    reason="NTFS file names are UTF-16 and may carry a lone surrogate")
def test_a_resume_recognises_a_file_named_with_a_lone_surrogate(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(central_logger, "setup_logging", lambda *a, **k: None)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_png(input_dir / "photo_\ud800.png")
    settings = settings_for((input_dir, tmp_path / "output"), "openai", "http://127.0.0.1:9",
                            ENABLE_LLM_INFERENCE=False, START_OVER=False)
    with caplog.at_level(logging.CRITICAL + 10):
        run_core(settings)
        events, db_path = run_core(settings)
    assert events[-1] == {"type": "done"}, events[-3:]
    connection = sqlite3.connect(db_path)
    try:
        registered = connection.execute("SELECT file_path FROM file_registry").fetchall()
    finally:
        connection.close()
    assert len(registered) == 1, f"the file was re-processed on resume: {registered}"
    assert "�" in registered[0][0]



def _ai_off_settings(sandbox, **overrides):
    from config_validator import SettingsAIDormant
    input_dir, output_dir = sandbox
    values = {
        "INPUT_FOLDER_PATH": str(input_dir),
        "OUTPUT_FOLDER_PATH": str(output_dir),
        "ENABLE_LLM_INFERENCE": False,
        "START_OVER": False,
    }
    values.update(overrides)
    return SettingsAIDormant(**values)


def test_a_dormant_output_mode_edit_never_refuses_an_ai_off_resume(tmp_path, monkeypatch):
    sandbox = _sandbox_with_one_photo(tmp_path, monkeypatch)
    events, _ = run_core(_ai_off_settings(sandbox, LLM_OUTPUT_MODE="table_per_file"))
    assert events[-1] == {"type": "done"}, events[-3:]
    events, db_path = run_core(_ai_off_settings(sandbox, LLM_OUTPUT_MODE="table_per_page"))
    assert events[-1] == {"type": "done"}, "a dormant field must not refuse the resume"
    connection = sqlite3.connect(db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM file_registry").fetchone() == (1,)
    finally:
        connection.close()


def test_dormant_garbage_in_the_output_mode_never_reaches_the_record(tmp_path, monkeypatch):
    sandbox = _sandbox_with_one_photo(tmp_path, monkeypatch)
    events, _ = run_core(_ai_off_settings(sandbox, LLM_OUTPUT_MODE=42))
    assert events[-1] == {"type": "done"}, events[-3:]
    events, db_path = run_core(_ai_off_settings(sandbox, LLM_OUTPUT_MODE=42))
    assert events[-1] == {"type": "done"}, "identical settings must resume into each other"
    from db_controller import SQLiteDatabaseController
    controller = SQLiteDatabaseController(db_path)
    try:
        recorded = controller.get_run_configuration()
    finally:
        controller.close()
    assert recorded is not None and recorded.ai_enabled is False
    assert recorded.output_mode in ("", None), (
        f"a dormant value was recorded as the run's output mode: {recorded.output_mode!r}")


def test_switching_ai_on_after_an_ai_off_run_is_refused_before_any_upload(
        tmp_path, monkeypatch):
    from schemas import ConfigurationError
    sandbox = _sandbox_with_one_photo(tmp_path, monkeypatch)
    events, _ = run_core(_ai_off_settings(sandbox))
    assert events[-1] == {"type": "done"}, events[-3:]
    point_tokens_at_the_fake(monkeypatch, "openai")
    server = make_server("openai", answer='{"answer": "paid"}').start()
    try:
        with pytest.raises(ConfigurationError) as refused:
            run_core(settings_for(sandbox, "openai", server.base_url,
                                  LLM_OUTPUT_COLUMNS="answer", START_OVER=False))
        assert len(server.requests) == 0, "refused before any upload"
    finally:
        server.stop()
    message = str(refused.value)
    assert message.startswith("i18n:err_resume_configuration_changed|"), message
    assert "ai_enabled: off -> on" in message
    assert refused.value.setting_field == "START_OVER"



@pytest.mark.parametrize("missing", ["processing_complete", "ai_enabled", "processing_error"])
def test_populated_database_without_completion_metadata_is_refused_without_inventing_history(tmp_path, missing):
    from contextlib import closing
    from db_controller import SQLiteDatabaseController
    path = tmp_path / "state.db"
    controller = SQLiteDatabaseController(path)
    controller.handle_file_started(1, "retained.png", ".png", "Generated")
    controller.record_run_configuration(False, "", ())
    controller.close()
    with closing(sqlite3.connect(path)) as connection:
        for view in ("overall_result", "file_to_llm_status", "file_to_jpegs_status"):
            connection.execute(f"DROP VIEW {view}")
        connection.execute(f"ALTER TABLE file_registry DROP COLUMN {missing}")
        connection.commit()
        before = connection.execute("SELECT * FROM file_registry").fetchall()
    with pytest.raises(ConfigurationError, match="err_resume_old_database"):
        SQLiteDatabaseController(path)
    with closing(sqlite3.connect(path)) as connection:
        assert missing not in {r[1] for r in connection.execute("PRAGMA table_info(file_registry)")}
        assert connection.execute("SELECT * FROM file_registry").fetchall() == before


def test_an_empty_database_can_initialize_completion_metadata(tmp_path):
    from db_controller import SQLiteDatabaseController
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE file_registry (file_id INTEGER PRIMARY KEY, file_path TEXT NOT NULL, "
                           "file_ext TEXT NOT NULL, total_pages INTEGER NOT NULL, file_to_jpegs_comment TEXT NOT NULL, "
                           "page_range TEXT NOT NULL, range_status TEXT NOT NULL)")
    controller = SQLiteDatabaseController(path)
    try:
        assert controller.get_highest_file_id() == 0
        controller.handle_file_started(1, "new.png", ".png", "Generated")
        assert controller.get_file_statuses(1)["overall_result"] == "fail"
    finally:
        controller.close()


LEGACY_RECOVERY_SCHEMA = """
CREATE TABLE file_registry (file_id INTEGER PRIMARY KEY, file_path TEXT NOT NULL, file_ext TEXT NOT NULL,
 total_pages INTEGER NOT NULL, file_to_jpegs_comment TEXT NOT NULL,
 page_range TEXT NOT NULL, range_status TEXT NOT NULL);
CREATE TABLE page_log (page_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, page_number INTEGER NOT NULL,
 output_file TEXT NOT NULL, page_to_jpeg_status TEXT NOT NULL, page_to_jpeg_comment TEXT NOT NULL,
 video_frame_timestamp TEXT NOT NULL);
CREATE TABLE llm_requests (request_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, request_number INTEGER NOT NULL,
 pages TEXT NOT NULL, request_status TEXT NOT NULL, raw_llm_answer TEXT NOT NULL, llm_network_error TEXT NOT NULL);
CREATE TABLE llm_answers (llm_answer_id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL, file_id INTEGER NOT NULL,
 page_id INTEGER, raw_model_page_number TEXT NOT NULL, llm_error TEXT NOT NULL, values_json TEXT NOT NULL);
CREATE TABLE run_configuration (configuration_id INTEGER PRIMARY KEY, ai_enabled BOOLEAN NOT NULL,
 output_mode TEXT NOT NULL, declared_columns TEXT NOT NULL);
CREATE TABLE ai_work (file_id INTEGER PRIMARY KEY, source_root TEXT NOT NULL, context_digest TEXT NOT NULL,
 frames_json TEXT NOT NULL);
CREATE TABLE ai_request_work (file_id INTEGER NOT NULL, request_number INTEGER NOT NULL, request_id INTEGER NOT NULL,
 PRIMARY KEY(file_id,request_number));
"""
LEGACY_RECOVERY_VIEWS = (
    """DROP VIEW IF EXISTS file_to_jpegs_status""",
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
    """DROP VIEW IF EXISTS file_to_llm_status""",
    """
    CREATE VIEW file_to_llm_status AS
    SELECT f.file_id,
           CASE
               WHEN c.total IS NULL AND w.file_id IS NOT NULL
                    AND w.frames_json != '[]' THEN 'llm_failure'
               WHEN c.total IS NULL THEN 'skipped'
               WHEN c.ok = c.total AND COALESCE(a.errors, 0) = 0 THEN 'ok'
               WHEN c.ok > 0 THEN 'partial_llm_failure'
               ELSE 'llm_failure'
           END AS file_to_llm_status
    FROM file_registry f
    LEFT JOIN ai_work w ON w.file_id = f.file_id
    LEFT JOIN (
        SELECT r.file_id, COUNT(*) AS total,
               SUM(CASE WHEN request_status = 'ok' THEN 1 ELSE 0 END) AS ok
        FROM llm_requests r
        LEFT JOIN ai_request_work rw ON rw.file_id = r.file_id
            AND rw.request_number = r.request_number
        WHERE rw.request_id IS NULL OR rw.request_id = r.request_id
        GROUP BY r.file_id
    ) c ON c.file_id = f.file_id
    LEFT JOIN (
        SELECT a.file_id,
               SUM(CASE WHEN llm_error != '' THEN 1 ELSE 0 END) AS errors
        FROM llm_answers a
        JOIN llm_requests r ON r.request_id = a.request_id
        LEFT JOIN ai_request_work rw ON rw.file_id = r.file_id
            AND rw.request_number = r.request_number
        WHERE rw.request_id IS NULL OR rw.request_id = r.request_id
        GROUP BY a.file_id
    ) a ON a.file_id = f.file_id
    """,
    """DROP VIEW IF EXISTS overall_result""",
    """
    CREATE VIEW overall_result AS
    SELECT j.file_id,
           CASE
               WHEN j.file_to_jpegs_status = 'skipped' THEN 'skipped'
               WHEN j.file_to_jpegs_status = 'processing_failure' THEN 'fail'
               WHEN j.file_to_jpegs_status = 'ok'
                    AND l.file_to_llm_status IN ('ok', 'skipped') THEN 'ok'
               ELSE 'partial_fail'
           END AS overall_result
    FROM file_to_jpegs_status j
    JOIN file_to_llm_status l ON l.file_id = j.file_id
    """,
)


@pytest.mark.parametrize("ai_enabled", [False, True])
def test_previous_recovery_database_refuses_writes_but_still_exports(tmp_path, ai_enabled):
    from db_controller import SQLiteDatabaseController
    from data_exporter import SQLiteDataExporter
    path = tmp_path / "previous.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(LEGACY_RECOVERY_SCHEMA)
        for statement in LEGACY_RECOVERY_VIEWS:
            connection.execute(statement)
        connection.execute("INSERT INTO file_registry VALUES (1,'retained.png','.png',1,'done','1','ok')")
        connection.execute("INSERT INTO page_log VALUES (1,1,1,'1.jpg','ok','','')")
        connection.execute("INSERT INTO run_configuration VALUES (1,?,?,?)",
                           (int(ai_enabled), "table_per_file" if ai_enabled else "",
                            '["answer"]' if ai_enabled else "[]"))
        if ai_enabled:
            connection.execute("INSERT INTO ai_work VALUES (1,'old-root','old-digest','[]')")
            connection.execute("INSERT INTO llm_requests VALUES (1,1,1,'1','interrupted','','uncertain')")
            connection.execute("INSERT INTO llm_requests VALUES (2,1,1,'1','ok','legacy answer','')")
            connection.execute("INSERT INTO llm_answers VALUES (1,2,1,NULL,'','',?)",
                               ('{"answer":"legacy answer"}',))
            connection.execute("INSERT INTO ai_request_work VALUES (1,1,2)")
        connection.commit()
        before = list(connection.iterdump())
    with pytest.raises(ConfigurationError, match="err_resume_old_database"):
        SQLiteDatabaseController(path)
    outcome = SQLiteDataExporter(path).export_all_formats(tmp_path / "reports")
    assert outcome.as_dict()["status"] == "success" and not outcome.recovery
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT file_path FROM file_registry").fetchall() == [("retained.png",)]
        assert "processing_complete" not in {r[1] for r in connection.execute("PRAGMA table_info(file_registry)")}
        assert list(connection.iterdump()) == before
    if ai_enabled:
        import csv
        result = next(item for item in outcome.saved if item["report"] == "Results" and item["format"] == "csv")
        with Path(result["path"]).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert [row["llm_answer"] for row in rows] == ["", "legacy answer"]
        assert "Request 1, attempt 1 of 2" in rows[0]["notes"]
        assert "Request 1, attempt 2 of 2" in rows[1]["notes"]


def test_an_empty_older_database_acquires_the_same_indexes_as_a_fresh_one(tmp_path):
    from contextlib import closing
    from db_controller import SQLiteDatabaseController

    def indexes(path):
        with closing(sqlite3.connect(path)) as connection:
            return {row[0]: row[1] for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    older = tmp_path / "older.db"
    with closing(sqlite3.connect(older)) as connection:
        connection.executescript(LEGACY_RECOVERY_SCHEMA)
    SQLiteDatabaseController(older).close()
    SQLiteDatabaseController(tmp_path / "fresh.db").close()
    introduced = ("ix_llm_requests_file_request", "ix_llm_requests_file_number", "ix_page_log_file_page")
    fresh = {name: sql for name, sql in indexes(tmp_path / "fresh.db").items() if name in introduced}
    assert set(fresh) == set(introduced) and "UNIQUE" in fresh["ix_llm_requests_file_request"]
    assert {name: sql for name, sql in indexes(older).items() if name in introduced} == fresh


@pytest.mark.parametrize("missing", ["file_registry", "page_log", "llm_requests", "llm_answers", "run_configuration"])
def test_populated_history_with_a_missing_table_is_not_reconstructed(tmp_path, missing):
    from db_controller import SQLiteDatabaseController
    from schemas import FileSummary, PageResult
    path = tmp_path / "damaged.db"
    controller = SQLiteDatabaseController(path)
    controller.record_run_configuration(False, "", ())
    controller.handle_file_started(1, "saved.png", ".png", "Generated")
    controller.handle_frame_saved(1, PageResult(1, "1.jpg", "ok", ""))
    controller.handle_file_completed(1, FileSummary(1, "1", "ok", "done"))
    controller.finalize_file(1)
    controller.close()
    with sqlite3.connect(path) as connection:
        for view in ("overall_result", "file_to_llm_status", "file_to_jpegs_status"):
            connection.execute(f"DROP VIEW {view}")
        connection.execute(f"DROP TABLE {missing}")
        connection.commit()
        before = list(connection.iterdump())
    with pytest.raises(ConfigurationError, match="err_resume_old_database"):
        SQLiteDatabaseController(path)
    with sqlite3.connect(path) as connection:
        assert list(connection.iterdump()) == before



def _schema(path):
    from contextlib import closing
    with closing(sqlite3.connect(path)) as connection:
        tables = [name for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {
            "tables": {table: [tuple(row[1:]) for row in connection.execute(f"PRAGMA table_info({table})")]
                       for table in tables},
            "indexes": {(table, row[1], row[2], tuple(column[2] for column in connection.execute(
                            f"PRAGMA index_info({row[1]})")))
                        for table in tables for row in connection.execute(f"PRAGMA index_list({table})")},
            "views": dict(connection.execute("SELECT name, sql FROM sqlite_master WHERE type='view'").fetchall()),
        }


EMPTY_OLDER = {"pre-pages": OLD_SCHEMA, "v0.1.0": V010_SCHEMA, "previous recovery writer": LEGACY_RECOVERY_SCHEMA}


@pytest.mark.parametrize("specimen", sorted(EMPTY_OLDER))
def test_an_empty_older_database_is_rebuilt_as_a_fresh_one(tmp_path, specimen):
    from contextlib import closing
    from db_controller import SQLiteDatabaseController
    from schemas import RequestOutcome
    from sqlalchemy.exc import IntegrityError
    older = tmp_path / "older.db"
    with closing(sqlite3.connect(older)) as connection:
        connection.executescript(EMPTY_OLDER[specimen])
    SQLiteDatabaseController(tmp_path / "fresh.db").close()
    controller = SQLiteDatabaseController(older)
    try:
        rebuilt = _schema(older)
        assert rebuilt == _schema(tmp_path / "fresh.db")
        assert not {"databasefileregistry", "databasepagelog", "ai_work", "ai_request_work"} & rebuilt["tables"].keys()
        assert ("llm_requests", "ix_llm_requests_file_request", 1, ("file_id", "request_number")) in rebuilt["indexes"]
        controller.handle_file_started(1, "new.png", ".png", "Generated")
        controller.handle_llm_requests(1, [RequestOutcome(1, (1,), "ok", "first", "")])
        with pytest.raises(IntegrityError):
            controller.handle_llm_requests(1, [RequestOutcome(1, (1,), "ok", "second", "")])
    finally:
        controller.close()


def test_an_interrupted_rebuild_is_completed_on_the_next_opening(tmp_path, monkeypatch):
    from contextlib import closing
    from sqlmodel import SQLModel
    from db_controller import SQLiteDatabaseController
    older = tmp_path / "older.db"
    with closing(sqlite3.connect(older)) as connection:
        connection.executescript(LEGACY_RECOVERY_SCHEMA)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    with monkeypatch.context() as patch:
        patch.setattr(SQLModel.metadata, "create_all", fail)
        with pytest.raises(sqlite3.OperationalError):
            SQLiteDatabaseController(older)
    SQLiteDatabaseController(tmp_path / "fresh.db").close()
    SQLiteDatabaseController(older).close()
    assert _schema(older) == _schema(tmp_path / "fresh.db")


def test_rebuilding_keeps_the_recorded_run_configuration(tmp_path):
    from contextlib import closing
    from db_controller import SQLiteDatabaseController
    from schemas import RunConfiguration
    older = tmp_path / "older.db"
    with closing(sqlite3.connect(older)) as connection:
        connection.executescript(LEGACY_RECOVERY_SCHEMA)
        connection.execute("INSERT INTO run_configuration VALUES (1, 1, 'table_per_page', ?)",
                           ('["answer", "page"]',))
        connection.commit()
    controller = SQLiteDatabaseController(older)
    try:
        assert controller.get_run_configuration() == RunConfiguration(True, "table_per_page", ("answer", "page"))
    finally:
        controller.close()
    with closing(sqlite3.connect(older)) as connection:
        assert connection.execute("SELECT source_root, processing_digest FROM run_configuration").fetchall() == [
            ("", "")]


def _current_database_with_one_saved_file(path):
    from db_controller import SQLiteDatabaseController
    controller = SQLiteDatabaseController(path)
    controller.record_run_configuration(False, "", ())
    controller.handle_file_started(1, "saved.png", ".png", "Generated")
    controller.close()


@pytest.mark.parametrize("difference", ["missing unique request index", "undeclared column",
                                        "saved rows in an undeclared table"])
def test_a_populated_database_unlike_the_declared_schema_is_refused_unchanged(tmp_path, difference):
    from contextlib import closing
    from db_controller import SQLiteDatabaseController
    path = tmp_path / "state.db"
    _current_database_with_one_saved_file(path)
    with closing(sqlite3.connect(path)) as connection:
        if difference == "missing unique request index":
            connection.execute("DROP INDEX ix_llm_requests_file_request")
        elif difference == "undeclared column":
            connection.execute("ALTER TABLE page_log ADD COLUMN unexpected TEXT")
        else:
            connection.executescript(V010_SCHEMA)
            connection.execute(OLDER_POPULATED["v0.1.0"][1])
        connection.commit()
        before = list(connection.iterdump())
    with pytest.raises(ConfigurationError, match="err_resume_old_database"):
        SQLiteDatabaseController(path)
    with closing(sqlite3.connect(path)) as connection:
        assert list(connection.iterdump()) == before


@pytest.mark.parametrize("extra", ["none", "an empty undeclared table"])
def test_a_populated_database_matching_the_declared_schema_opens_with_its_rows(tmp_path, extra):
    from contextlib import closing
    from db_controller import SQLiteDatabaseController
    path = tmp_path / "state.db"
    _current_database_with_one_saved_file(path)
    if extra != "none":
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(V010_SCHEMA)
    controller = SQLiteDatabaseController(path)
    try:
        assert controller.get_highest_file_id() == 1
    finally:
        controller.close()
