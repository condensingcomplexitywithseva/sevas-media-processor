# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import ast
import csv
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import ClassVar

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import central_logger
import config_loader
import routes.execution_api as exec_api
from config_loader import ConfigManager, TokenManager
from config_validator import tech_folder_path
from db_controller import SQLiteDatabaseController
from routes.web_server import (
    create_app,
    is_open_path,
    OPEN_PATHS,
    OPEN_PREFIXES,
    SESSION_TOKEN,
)
from schemas import FileSummary, PageResult, Status

AUTH = {"X-App-Token": SESSION_TOKEN}



@pytest.fixture
def api(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.endswith("_TOKEN"):
            monkeypatch.delenv(key)

    monkeypatch.setattr(central_logger, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(central_logger, "_configured", False)

    mgr = ConfigManager(tmp_path)
    mgr.app_data_dir = tmp_path
    mgr.env_path = tmp_path / ".env"
    mgr.token_manager = TokenManager(tmp_path / ".env")
    (tmp_path / "input").mkdir()
    monkeypatch.setattr(config_loader, "_manager", mgr)

    monkeypatch.setattr(exec_api, "run_controller", exec_api.RunController())
    exec_api.run_controller.abort.clear()

    def build(**overrides):
        payload = {
            "INPUT_FOLDER_PATH": str(tmp_path / "input"),
            "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
        }
        payload.update(overrides)
        mgr.settings_path.write_text(json.dumps(payload), encoding="utf-8")
        return create_app()

    setattr(build, "mgr", mgr)  # noqa: B010  (for tests that seed the sandbox .env / paths)
    yield build
    exec_api.run_controller.abort.clear()


@pytest.fixture
def fake_core(monkeypatch):
    class FakeCore:
        release = threading.Event()
        instances: ClassVar[list] = []

        def __init__(self, settings, abort_flag, on_progress=None):
            FakeCore.instances.append(self)

        def run(self):
            FakeCore.release.wait(timeout=30)

    monkeypatch.setattr(exec_api, "ProcessorCore", FakeCore)
    yield FakeCore
    FakeCore.release.set()
    thread = exec_api.run_controller.thread
    if thread is not None:
        thread.join(timeout=5)
        assert not thread.is_alive(), "fake run failed to shut down"


def start_run(client):
    response = client.post("/api/process/start", headers=AUTH)
    assert response.status_code == 200, response.get_json()
    return response



def test_forged_host_header_is_rejected_everywhere(api):
    client = api().test_client()
    forged = {"Host": "attacker.example"}
    assert client.get("/", headers=forged).status_code == 403
    assert client.get(
        "/api/process/status", headers={**forged, **AUTH}
    ).status_code == 403


def test_api_without_session_token_is_forbidden(api):
    client = api().test_client()
    assert client.get("/api/process/status").status_code == 403
    assert client.get(
        "/api/process/status", headers={"X-App-Token": "wrong-guess"}
    ).status_code == 403


def test_api_with_header_token_is_allowed(api):
    client = api().test_client()
    response = client.get("/api/process/status", headers=AUTH)
    assert response.status_code == 200


def test_the_query_token_opens_the_event_stream_only(api):
    client = api().test_client()
    stream = client.get("/api/process/stream", query_string={"token": SESSION_TOKEN})
    assert stream.status_code == 200
    stream.close()
    assert client.get(
        "/api/process/status", query_string={"token": SESSION_TOKEN}
    ).status_code == 403
    assert client.get("/api/process/stream").status_code == 403
    assert client.get(
        "/api/process/stream", query_string={"token": "wrong-guess"}
    ).status_code == 403
    assert client.get("/api/process/status", headers=AUTH).status_code == 200


def test_page_route_needs_no_token_on_loopback_hosts(api):
    client = api().test_client()
    assert client.get("/").status_code == 200
    assert client.get("/", headers={"Host": "127.0.0.1:5000"}).status_code == 200


def test_static_assets_are_served_without_a_token(api):
    client = api().test_client()
    assert client.get("/static/app.js").status_code == 200


def test_the_session_token_is_never_rendered_into_the_page(api):
    page = api().test_client().get("/").get_data(as_text=True)

    assert SESSION_TOKEN not in page
    assert "__receiveApiToken" in page, (
        "the page no longer exposes the hook main.py pushes the token through"
    )

    leaked = [
        candidate for candidate in re.findall(r"[A-Za-z0-9_-]{40,}", page)
        if not candidate.startswith("data:")
    ]
    assert not leaked, f"the page contains secret-shaped strings: {leaked[:5]}"


def concrete_routes(app):
    for rule in app.url_map.iter_rules():
        methods = sorted(rule.methods - {"HEAD", "OPTIONS"})
        if not methods:
            continue
        url = rule.rule
        for argument in rule.arguments:
            url = re.sub(rf"<[^<>]*\b{re.escape(argument)}>", "probe", url)
        yield methods[0], url


def test_every_route_except_the_open_list_requires_the_token(api):
    app = api()
    client = app.test_client()
    routes = list(concrete_routes(app))

    assert len(routes) >= 15, f"route sweep found only {len(routes)}; is it walking the map?"

    unprotected = [
        (method, url, client.open(url, method=method).status_code)
        for method, url in routes
        if not is_open_path(url)
    ]
    unprotected = [row for row in unprotected if row[2] != 403]

    assert not unprotected, (
        "these routes answered without a token; every route outside "
        f"OPEN_PATHS/OPEN_PREFIXES must be refused: {unprotected}"
    )


def test_a_route_added_outside_api_is_protected_by_default(api):
    app = api()

    @app.route("/newly-added-feature")
    def _probe():
        return "sensitive"

    client = app.test_client()
    assert client.get("/newly-added-feature").status_code == 403
    assert client.get("/newly-added-feature", headers=AUTH).status_code == 200


def test_widening_the_unauthenticated_surface_is_deliberate(api):
    assert OPEN_PATHS == ("/",)
    assert OPEN_PREFIXES == ("/static/",)



def test_second_start_while_running_is_rejected(api, fake_core):
    client = api().test_client()
    start_run(client)

    response = client.post("/api/process/start", headers=AUTH)
    assert response.status_code == 400
    assert len(fake_core.instances) == 1
    assert response.get_json().get("message_key") == "err_run_active"


def test_simultaneous_starts_admit_exactly_one(api, fake_core):
    app = api()
    barrier = threading.Barrier(2)
    status_codes = []

    def fire():
        client = app.test_client()
        barrier.wait(timeout=5)
        status_codes.append(client.post("/api/process/start", headers=AUTH).status_code)

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert sorted(status_codes) == [200, 400]
    assert len(fake_core.instances) == 1



def test_start_with_invalid_settings_returns_error_details(api, fake_core):
    client = api(JPEG_QUALITY=500).test_client()

    response = client.post("/api/process/start", headers=AUTH)
    assert response.status_code == 400
    body = response.get_json()
    assert "JPEG_QUALITY" in body["errors"]

    assert fake_core.instances == []
    snapshot = client.get("/api/process/status", headers=AUTH).get_json()
    assert not snapshot["is_running"] and not snapshot["is_stopping"]
    assert snapshot["phase"] == "idle" and snapshot["run_id"] == ""



def test_stop_without_active_run_is_rejected(api):
    client = api().test_client()
    response = client.post("/api/process/stop", headers=AUTH)
    assert response.status_code == 409


def test_status_and_stop_track_the_run_lifecycle(api, fake_core):
    client = api().test_client()

    def status():
        snapshot = client.get("/api/process/status", headers=AUTH).get_json()
        return {key: snapshot[key] for key in ("is_running", "is_stopping")}

    assert status() == {"is_running": False, "is_stopping": False}

    start_run(client)
    assert status() == {"is_running": True, "is_stopping": False}

    response = client.post("/api/process/stop", headers={**AUTH, "X-Run-Id": exec_api.run_controller.run_id})
    assert response.status_code == 200
    assert exec_api.run_controller.abort.is_set()
    assert exec_api.run_controller.thread is not None
    assert exec_api.run_controller.thread.is_alive()
    assert status() == {"is_running": True, "is_stopping": True}

    fake_core.release.set()
    assert exec_api.run_controller.thread is not None
    exec_api.run_controller.thread.join(timeout=5)
    assert status() == {"is_running": False, "is_stopping": False}



def test_export_refuses_while_a_run_is_active(api, fake_core):
    client = api().test_client()
    start_run(client)

    response = client.post("/api/export/database", headers=AUTH)
    assert response.status_code == 409
    assert response.get_json()["message_key"] == "err_export_run_active"


def test_export_with_no_output_configured_is_400(api):
    client = api(OUTPUT_FOLDER_PATH="").test_client()
    response = client.post("/api/export/database", headers=AUTH)
    assert response.status_code == 400
    assert response.get_json()["message_key"] == "err_export_no_folder"
    assert response.get_json()["export_state"] == "refused"
    assert response.get_json()["refs"] == {"output": "OUTPUT_FOLDER_PATH"}


def test_export_without_a_database_is_404(api):
    client = api().test_client()
    response = client.post("/api/export/database", headers=AUTH)
    assert response.status_code == 404
    assert response.get_json()["message_key"] == "err_export_no_results"


def test_export_success_writes_files_and_releases_the_db(api, tmp_path):
    output_folder = tmp_path / "output"
    db_path = tech_folder_path(output_folder) / "application_state.db"
    db_path.parent.mkdir(parents=True)

    controller = SQLiteDatabaseController(db_path)
    controller.handle_file_started(1, "docs/report.pdf", ".pdf", "TestPipeline")
    controller.handle_frame_saved(
        1, PageResult(1, "0001_p001.jpg", Status.OK.value, "")
    )
    controller.handle_file_completed(
        1, FileSummary(1, "1", "ok", "done")
    )
    controller.finalize_file(1)
    controller.record_run_configuration(False, "", ())
    controller.close()

    client = api().test_client()
    response = client.post("/api/export/database", headers=AUTH)
    assert response.status_code == 200, response.get_json()

    export_dir = output_folder / "exports"
    registry_csv = next(export_dir.glob("file_registry_*.csv"), None)
    page_log_csv = next(export_dir.glob("page_log_*.csv"), None)
    assert registry_csv is not None, "export is missing the registry CSV"
    assert page_log_csv is not None, "export is missing the page-log CSV"
    assert next(export_dir.glob("database_export_*.xlsx"), None) is not None, (
        "export is missing the XLSX workbook"
    )

    with open(registry_csv, newline="", encoding="utf-8") as f:
        registry_rows = list(csv.DictReader(f))
    assert [r["file_path"] for r in registry_rows] == ["docs/report.pdf"]
    assert registry_rows[0]["overall_result"] == "ok"

    with open(page_log_csv, newline="", encoding="utf-8") as f:
        page_rows = list(csv.DictReader(f))
    assert [r["output_file"] for r in page_rows] == ["0001_p001.jpg"]

    moved = db_path.with_name("renamed_ok.db")
    os.rename(db_path, moved)
    assert moved.exists()


@pytest.fixture(autouse=True)
def explorer_spy(monkeypatch):
    import routes.export_api as export_api

    seen = []
    monkeypatch.setattr(export_api.subprocess, "Popen",
                        lambda cmd, *a, **k: seen.append(cmd))
    return seen


def test_db_export_reveals_the_xlsx_in_explorer(api, tmp_path, explorer_spy):
    db_path = tech_folder_path(tmp_path / "output") / "application_state.db"
    db_path.parent.mkdir(parents=True)
    controller = SQLiteDatabaseController(db_path)
    controller.record_run_configuration(False, "", ())
    controller.close()

    client = api().test_client()
    response = client.post("/api/export/database", headers=AUTH)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["revealed"] is True

    assert len(explorer_spy) == 1
    executable, argument = explorer_spy[0][0], explorer_spy[0][1]
    assert Path(executable).is_absolute(), f"bare name is plantable: {executable}"
    assert Path(executable).name.lower() == "explorer.exe"
    revealed = Path(argument.removeprefix("/select,"))
    assert revealed.parent == tmp_path / "output" / "exports"
    assert revealed.name.startswith("database_export_") and revealed.suffix == ".xlsx"


def test_log_export_reveals_the_exported_file_in_explorer(api, tmp_path, explorer_spy):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "system_log_2026-07-05.txt").write_text("lines", encoding="utf-8")

    client = api().test_client()
    response = client.post("/api/export/logs", headers=AUTH)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["revealed"] is True

    expected = tmp_path / "output" / "exports" / "system_log_2026-07-05.txt"
    assert explorer_spy[0][1] == "/select," + str(expected)


def test_open_logs_folder_opens_the_folder_not_a_selection(api, tmp_path, explorer_spy):
    (tmp_path / "logs").mkdir()

    client = api().test_client()
    response = client.post("/api/export/open_logs_folder", headers=AUTH)
    assert response.status_code == 200, response.get_json()

    assert len(explorer_spy) == 1
    executable, argument = explorer_spy[0][0], explorer_spy[0][1]
    assert Path(executable).is_absolute(), f"bare name is plantable: {executable}"
    assert Path(executable).name.lower() == "explorer.exe"
    assert argument == str(tmp_path / "logs")


def test_open_logs_folder_reports_failure_with_a_translatable_key(api, tmp_path, monkeypatch):
    import routes.export_api as export_api

    def no_explorer(*args, **kwargs):
        raise FileNotFoundError(2, "no explorer here")

    monkeypatch.setattr(export_api.subprocess, "Popen", no_explorer)
    (tmp_path / "logs").mkdir()

    client = api().test_client()
    response = client.post("/api/export/open_logs_folder", headers=AUTH)
    assert response.status_code == 500
    assert response.get_json()["message_key"] == "err_open_folder_failed"


def test_open_logs_folder_without_a_logs_folder_is_404(api, explorer_spy):
    client = api().test_client()
    response = client.post("/api/export/open_logs_folder", headers=AUTH)
    assert response.status_code == 404
    assert response.get_json()["message_key"] == "err_export_no_log"
    assert explorer_spy == []


def test_a_missing_explorer_never_turns_a_green_export_red(api, tmp_path, monkeypatch):
    import routes.export_api as export_api

    def no_explorer(*args, **kwargs):
        raise FileNotFoundError(2, "no explorer here")

    monkeypatch.setattr(export_api.subprocess, "Popen", no_explorer)

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "system_log_2026-07-05.txt").write_text("lines", encoding="utf-8")

    client = api().test_client()
    response = client.post("/api/export/logs", headers=AUTH)

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["status"] == "success"
    assert body["revealed"] is False
    assert (tmp_path / "output" / "exports" / "system_log_2026-07-05.txt").exists()



def test_log_export_with_no_output_configured_is_400(api):
    client = api(OUTPUT_FOLDER_PATH="").test_client()
    response = client.post("/api/export/logs", headers=AUTH)
    assert response.status_code == 400
    assert response.get_json()["message_key"] == "err_export_logs_no_folder"


def test_log_export_without_any_log_is_404(api):
    client = api().test_client()
    response = client.post("/api/export/logs", headers=AUTH)
    assert response.status_code == 404
    assert response.get_json()["message_key"] == "err_export_no_log"


def test_log_export_copies_the_newest_log_and_returns_its_path(api, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    older = logs_dir / "system_log_2026-07-01.txt"
    newest = logs_dir / "system_log_2026-07-05.txt"
    older.write_text("stale lines", encoding="utf-8")
    newest.write_text("current session lines", encoding="utf-8")
    os.utime(older, (1, 1))

    client = api().test_client()
    response = client.post("/api/export/logs", headers=AUTH)
    assert response.status_code == 200, response.get_json()

    exported = tmp_path / "output" / "exports" / newest.name
    assert exported.exists(), "the newest log was not copied into <output>/exports"
    assert exported.read_text(encoding="utf-8") == "current session lines"
    assert response.get_json()["path"] == str(exported)
    assert newest.exists()


def test_log_export_works_while_the_log_is_still_open_for_writing(api, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    live = logs_dir / "system_log_2026-07-05.txt"

    with open(live, "a", encoding="utf-8") as handle:
        handle.write("first line\n")
        handle.flush()
        client = api().test_client()
        response = client.post("/api/export/logs", headers=AUTH)

    assert response.status_code == 200, response.get_json()
    exported = tmp_path / "output" / "exports" / live.name
    assert exported.read_text(encoding="utf-8") == "first line\n"



def test_clear_logs_never_deletes_the_active_session_log(api, tmp_path, monkeypatch):
    import central_logger

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    old_a = logs_dir / "system_log_2026-07-01.txt"
    old_b = logs_dir / "system_log_2026-07-03.txt"
    active = logs_dir / "system_log_2026-07-05.txt"
    for log_file in (old_a, old_b, active):
        log_file.write_text("log lines", encoding="utf-8")
    monkeypatch.setattr(central_logger, "get_active_log_file", lambda: active.resolve())

    client = api().test_client()
    response = client.post("/api/export/clear_logs", headers=AUTH)

    assert response.status_code == 200
    assert active.exists()
    assert not old_a.exists() and not old_b.exists()



def test_rendered_page_never_contains_a_real_token(api):
    app = api()
    api.mgr.token_manager.update_tokens({"openai": "super-secret-token-not-for-html"})

    page = app.test_client().get("/").get_data(as_text=True)

    assert "super-secret-token-not-for-html" not in page
    assert "********" in page



CORRUPTED = '{ "JPEG_QUALITY": 42, "INPUT_FOLDER_PATH": "D:/photos"  <- comma missing'


def test_commit_refuses_when_settings_json_is_corrupted(api, tmp_path):
    client = api().test_client()
    (tmp_path / "settings.json").write_text(CORRUPTED, encoding="utf-8")

    draft = {
        "INPUT_FOLDER_PATH": str(tmp_path / "input"),
        "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
        "JPEG_QUALITY": 55,
    }
    response = client.post("/api/settings/commit", json=draft, headers=AUTH)

    assert response.status_code == 400
    body = response.get_json()
    assert body["status"] == "error"
    assert body["errors"]["general"]["value"] == "err_broken_json"

    survived = (tmp_path / "settings.json").read_text(encoding="utf-8")
    assert survived == CORRUPTED, "the user's only copy of their settings was overwritten"


def test_commit_still_works_on_a_readable_file(api, tmp_path):
    client = api(JPEG_QUALITY=500).test_client()

    response = client.post(
        "/api/settings/commit",
        json={
            "INPUT_FOLDER_PATH": str(tmp_path / "input"),
            "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
            "JPEG_QUALITY": 55,
        },
        headers=AUTH,
    )

    assert response.status_code == 200, response.get_json()
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["JPEG_QUALITY"] == 55



def test_open_file_spawns_notepad_by_absolute_path(api, tmp_path, monkeypatch):
    import routes.settings_api as settings_api

    spawned = []
    monkeypatch.setattr(settings_api.subprocess, "Popen", spawned.append)

    client = api().test_client()

    response = client.post("/api/settings/open_file", json={}, headers=AUTH)

    assert response.status_code == 200, response.get_json()
    assert len(spawned) == 1
    executable = Path(spawned[0][0])
    assert executable.is_absolute(), f"bare name is plantable: {executable}"
    assert executable.parent.name.lower() == "system32"
    assert executable.name.lower() == "notepad.exe"
    if sys.platform == "win32":
        assert executable.exists(), f"{executable} is not the real Notepad"

    assert Path(spawned[0][1]) == tmp_path / "settings.json"


def test_open_file_offers_the_backup_beside_the_active_settings(api, tmp_path,
                                                                monkeypatch):
    import routes.settings_api as settings_api

    spawned = []
    monkeypatch.setattr(settings_api.subprocess, "Popen", spawned.append)

    client = api().test_client()
    backup = tmp_path / "settings_corrupted_backup_20260808_120000.json"
    backup.write_text("{}", encoding="utf-8")

    response = client.post(
        "/api/settings/open_file", json={"target": "backup"}, headers=AUTH
    )

    assert response.status_code == 200, response.get_json()
    assert Path(spawned[0][1]) == backup


def test_a_missing_notepad_is_reported_with_the_path_to_open(api, tmp_path, monkeypatch):
    import routes.settings_api as settings_api

    def no_notepad(*args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(settings_api.subprocess, "Popen", no_notepad)

    client = api().test_client()

    response = client.post("/api/settings/open_file", json={}, headers=AUTH)

    assert response.status_code == 500
    body = response.get_json()
    assert body["message_key"] == "err_editor_launch_failed"
    assert body["path"] == str(tmp_path / "settings.json")

    locales = sorted((SRC / "locales").glob("*.json"))
    assert locales
    for locale in locales:
        strings = json.loads(locale.read_text(encoding="utf-8"))
        for key in ("err_editor_launch_failed", "err_settings_file_missing"):
            assert key in strings, f"{key} missing from {locale.name}"
            assert strings[key].rstrip().endswith("{path}"), (
                f"{key} in {locale.name} must end with the {{path}} placeholder"
            )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows search order")
def test_a_planted_notepad_would_have_won_and_no_longer_does(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    from routes.settings_api import _notepad_path

    search_path = ctypes.windll.kernel32.SearchPathW
    search_path.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                            wintypes.DWORD, wintypes.LPWSTR,
                            ctypes.POINTER(wintypes.LPWSTR)]
    search_path.restype = wintypes.DWORD

    planted = tmp_path / "notepad.exe"
    planted.write_bytes(b"MZ inert - never executed")
    monkeypatch.chdir(tmp_path)

    buffer = ctypes.create_unicode_buffer(32768)
    written = search_path(None, "notepad.exe", None, len(buffer), buffer, None)
    bare_name_resolves_to = Path(buffer.value) if written else None

    assert bare_name_resolves_to == planted, (
        "Windows did not prefer the planted file, so this machine cannot "
        f"demonstrate the attack (resolved to {bare_name_resolves_to})"
    )
    assert Path(_notepad_path()) != planted
    assert Path(_notepad_path()).parent.name.lower() == "system32"



def _preview(client, path):
    return client.post("/api/preview/file", json={"filepath": path}, headers=AUTH)


def test_preview_serves_the_top_of_a_txt_file(api, tmp_path):
    client = api().test_client()
    short = tmp_path / "prompt.txt"
    short.write_text("line one\nline two\n", encoding="utf-8")
    tall = tmp_path / "tall.TXT"
    tall.write_text("".join(f"row {n}\n" for n in range(25)), encoding="utf-8")

    body = _preview(client, str(short)).get_json()
    assert body["preview_type"] == "full"
    assert "line one" in body["content"]

    body = _preview(client, str(tall)).get_json()
    assert body["preview_type"] == "top10"
    assert "row 9" in body["content"]
    assert "row 10" not in body["content"]


def test_preview_refuses_non_txt_before_touching_disk(api, tmp_path):
    client = api().test_client()
    exists = tmp_path / "win.ini"
    exists.write_text("[fonts]\nSECRET=value\n", encoding="utf-8")
    missing = tmp_path / "nothing_here.ini"

    responses = [_preview(client, str(path)) for path in (exists, missing)]
    for response in responses:
        assert response.status_code == 400
        assert response.get_json()["preview_type"] == "error"
        assert response.get_json()["message_key"] == "preview_not_txt"
    assert "SECRET" not in responses[0].get_data(as_text=True)


def test_preview_missing_and_directory_paths_answer_the_locale_key(api, tmp_path):
    client = api().test_client()
    folder = tmp_path / "folder.txt"
    folder.mkdir()
    for path in ("", str(tmp_path / "missing.txt"), str(folder)):
        response = _preview(client, path)
        assert response.status_code == 400
        assert response.get_json()["preview_type"] == "error"
        assert response.get_json()["message_key"] == "preview_no_file"


def test_preview_read_failure_is_a_key_never_an_exception(api, tmp_path):
    client = api().test_client()
    utf16 = tmp_path / "prompt.txt"
    utf16.write_bytes("привет".encode("utf-16"))

    response = _preview(client, str(utf16))
    assert response.status_code == 500
    assert response.get_json()["preview_type"] == "error"
    assert response.get_json()["message_key"] == "preview_read_failed"
    assert "codec" not in response.get_data(as_text=True)


def test_preview_refuses_bytes_that_decode_but_are_not_text(api, tmp_path):
    client = api().test_client()
    bomless = tmp_path / "prompt.txt"
    bomless.write_bytes("secret words".encode("utf-16-le"))

    response = _preview(client, str(bomless))
    assert response.status_code == 500
    assert response.get_json()["preview_type"] == "error"
    assert response.get_json()["message_key"] == "preview_read_failed"
    assert "secret" not in response.get_data(as_text=True)


def test_preview_opens_via_the_long_path_spelling(api, tmp_path, monkeypatch):
    import routes.settings_api as settings_api
    from fs_utils import get_safe_path as real_get_safe_path

    recorded = []

    def recording(path_obj):
        recorded.append(Path(path_obj))
        return real_get_safe_path(path_obj)

    monkeypatch.setattr(settings_api, "get_safe_path", recording)

    client = api().test_client()
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("hello", encoding="utf-8")

    body = _preview(client, str(prompt)).get_json()
    assert body["content"] == "hello"
    assert recorded == [prompt]


def test_every_preview_key_the_endpoint_emits_is_translated():
    import inspect
    import routes.settings_api as settings_api

    emitted = set(re.findall(r'"(preview_[a-z_]+)"',
                             inspect.getsource(settings_api.preview_file)))
    emitted.discard("preview_type")
    assert emitted == {"preview_no_file", "preview_not_txt",
                       "preview_read_failed"}

    locales = sorted((SRC / "locales").glob("*.json"))
    assert locales
    for locale in locales:
        strings = json.loads(locale.read_text(encoding="utf-8"))
        for key in sorted(emitted):
            assert key in strings, f"{key} missing from {locale.name}"


def test_the_bare_name_sweep_can_actually_fail():
    flagged = [
        'subprocess.Popen(["notepad.exe", str(target)])',
        'subprocess.run(["notepad.exe", str(target)], check=True)',
        'subprocess.call(["notepad.exe"])',
        'subprocess.check_call(["notepad.exe"])',
        'subprocess.check_output(["notepad.exe"])',
        'subprocess.Popen("notepad.exe file.txt", shell=True)',
        'subprocess.run(f"notepad.exe {path}", shell=True)',
        'os.system("notepad.exe file.txt")',
        'os.popen("notepad.exe file.txt")',
        'os.startfile("notepad.exe")',
        'os.spawnv(os.P_NOWAIT, "notepad.exe", args)',
        'os.execvp("notepad.exe", args)',
        'import subprocess as sp\nsp.run(["notepad.exe", p])',
        'from subprocess import Popen as launch\nlaunch(["notepad.exe", p])',
    ]
    for snippet in flagged:
        assert _bare_name_spawns(snippet), f"sweep missed: {snippet!r}"

    cleared = [
        'subprocess.Popen([_notepad_path(), str(target)])',
        'subprocess.Popen([r"C:\\Windows\\System32\\notepad.exe", p])',
        'subprocess.run([sys.executable, "-m", "pip"], check=True)',
        'os.system(r"C:\\Windows\\System32\\notepad.exe file.txt")',
        'subprocess.run(f"{editor} {path}", shell=True)',
    ]
    for snippet in cleared:
        assert not _bare_name_spawns(snippet), f"sweep wrongly flagged: {snippet!r}"


_SPAWN_SUBPROCESS = {"Popen", "run", "call", "check_call", "check_output"}
_SPAWN_OS = {"system", "popen", "startfile"}


def _is_spawn_func(module, name):
    if module == "subprocess":
        return name in _SPAWN_SUBPROCESS
    if module == "os":
        return name in _SPAWN_OS or name.startswith(("spawn", "exec"))
    return False


def _spawn_calls(text: str):
    tree = ast.parse(text)

    module_aliases = {"subprocess": "subprocess", "os": "os"}
    func_aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ("subprocess", "os"):
                    module_aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in ("subprocess", "os"):
            for alias in node.names:
                func_aliases[alias.asname or alias.name] = (node.module, alias.name)

    def spawn_name(func):
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = module_aliases.get(func.value.id)
            if module and _is_spawn_func(module, func.attr):
                return f"{module}.{func.attr}"
        if isinstance(func, ast.Name) and func.id in func_aliases:
            module, real = func_aliases[func.id]
            if _is_spawn_func(module, real):
                return f"{module}.{real}"
        return None

    def bare_literal(arg):
        if isinstance(arg, (ast.List, ast.Tuple)):
            arg = arg.elts[0] if arg.elts else None
        if isinstance(arg, ast.JoinedStr):
            arg = arg.values[0] if arg.values else None
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            token = arg.value.split()[0] if arg.value.split() else ""
            if token and "\\" not in token and "/" not in token:
                return token
        return None

    spawns, bare = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = spawn_name(node.func)
        if name is None:
            continue
        spawns.append(name)
        index = 1 if name.startswith("os.spawn") else 0
        if len(node.args) > index:
            token = bare_literal(node.args[index])
            if token is not None:
                bare.append(f"line {node.lineno}: {name}({token!r}, ...)")
    return spawns, bare


def _bare_name_spawns(text: str) -> list:
    return _spawn_calls(text)[1]


def test_no_process_in_src_is_spawned_by_bare_name():
    all_spawns, offenders = [], []
    for path in sorted(SRC.rglob("*.py")):
        spawns, bare = _spawn_calls(path.read_text(encoding="utf-8"))
        all_spawns.extend(spawns)
        offenders.extend(f"{path.relative_to(SRC)} {item}" for item in bare)
    assert "subprocess.Popen" in all_spawns, (
        "the sweep no longer sees the settings-editor spawn - has it stopped working?"
    )
    assert not offenders, (
        "process spawned by a bare name Windows resolves through the "
        f"plantable search order: {offenders}"
    )



def test_about_open_link_opens_only_allowlisted_urls(api, monkeypatch):
    import webbrowser

    from version import APP_LINKS

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    client = api().test_client()

    r = client.post("/api/about/open_link", json={"target": "youtube"}, headers=AUTH)
    assert r.status_code == 200
    assert opened == [APP_LINKS["youtube"]]


def test_about_open_link_refuses_unknown_keys_and_raw_urls(api, monkeypatch):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    client = api().test_client()

    for bad in ("", "twitter", "https://evil.example", "file:///C:/Windows"):
        r = client.post("/api/about/open_link", json={"target": bad}, headers=AUTH)
        assert r.status_code == 404, f"key {bad!r} was not refused"
    assert opened == [], "a non-allowlisted value reached the browser"


def test_about_open_link_failure_answers_with_a_translated_key(api, monkeypatch):
    import webbrowser

    from version import APP_LINKS

    def boom(url):
        raise OSError("no default browser")

    monkeypatch.setattr(webbrowser, "open", boom)
    client = api().test_client()

    r = client.post("/api/about/open_link", json={"target": "github"}, headers=AUTH)
    assert r.status_code == 500
    body = r.get_json()
    assert body["message_key"] == "err_open_link_failed"
    assert body["url"] == APP_LINKS["github"]


def seed_export_run(tmp_path):
    database = tech_folder_path(tmp_path / "output") / "application_state.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    controller = SQLiteDatabaseController(database)
    controller.record_run_configuration(False, "", ())
    controller.handle_file_started(1, "original.png", ".png", "Test")
    controller.handle_frame_saved(1, PageResult(1, "one.jpg", "ok", ""))
    controller.handle_file_completed(1, FileSummary(1, "1", "ok", "done"))
    controller.close()
    return database


def test_export_failure_returns_saved_inventory_and_original_source_retry(api, tmp_path, monkeypatch):
    import data_exporter
    seed_export_run(tmp_path)
    app = api()
    client = app.test_client()
    original = data_exporter.SQLiteDataExporter._workbook
    def fail(*args, **kwargs):
        raise RuntimeError("workbook failure")
    monkeypatch.setattr(data_exporter.SQLiteDataExporter, "_workbook", fail)
    response = client.post("/api/export/database", headers=AUTH)
    assert response.status_code == 207
    data = response.get_json()
    assert len(data["saved"]) == 3 and data["failed"][0]["report"] == "Workbook"
    assert data["recovery_id"] and all(Path(r["path"]).is_file() for r in data["saved"])
    assert "database" not in data
    config_loader._manager.settings_path.write_text(json.dumps({
        "INPUT_FOLDER_PATH": str(tmp_path / "input"), "OUTPUT_FOLDER_PATH": str(tmp_path / "different")
    }), encoding="utf-8")
    monkeypatch.setattr(data_exporter.SQLiteDataExporter, "_workbook", original)
    destination = tmp_path / "recovered"
    recovered = client.post("/api/export/database", headers=AUTH, json={
        "recovery_id": data["recovery_id"], "destination": str(destination)})
    assert recovered.status_code == 200, recovered.get_json()
    final = recovered.get_json()
    assert len(final["saved"]) == 4 and final["failed"] == []
    assert all(Path(r["path"]).parent == destination for r in final["saved"])
    with next(destination.glob("results_*.csv")).open(encoding="utf-8", newline="") as handle:
        assert next(iter(csv.DictReader(handle)))["file_path"] == "original.png"
    assert not (tmp_path / "different").exists()


def test_recovery_rejects_replaced_source_and_forged_ids(api, tmp_path):
    database = seed_export_run(tmp_path)
    client = api().test_client()
    data = client.post("/api/export/database", headers=AUTH).get_json()
    archived = database.with_suffix(".archived")
    database.rename(archived)
    replacement = SQLiteDatabaseController(database)
    replacement.record_run_configuration(False, "", ())
    replacement.close()
    for token in (data["recovery_id"], "invented-token"):
        response = client.post("/api/export/database", headers=AUTH, json={"recovery_id": token})
        assert response.status_code == 409
        assert response.get_json()["message_key"] == "err_export_source_changed"


def test_recovery_also_refuses_during_processing(api, fake_core, tmp_path):
    seed_export_run(tmp_path)
    client = api().test_client()
    data = client.post("/api/export/database", headers=AUTH).get_json()
    start_run(client)
    response = client.post("/api/export/database", headers=AUTH, json={"recovery_id": data["recovery_id"]})
    assert response.status_code == 409 and response.get_json()["message_key"] == "err_export_run_active"


def test_export_start_lock_refuses_overlap_without_waiting(api):
    client = api().test_client()
    with exec_api.run_controller.lock:
        response = client.post("/api/export/database", headers=AUTH)
    assert response.status_code == 409



def test_message_envelope_preserves_domain_payload_and_literal_detail():
    from schemas import message_envelope
    reports = [{"format": "csv", "path": "C:/reports/Results.csv"}]
    data = message_envelope({"status": "error", "message": "i18n:err_settings_invalid|literal | detail",
                             "field": "DOCUMENT_RANGE", "saved": reports, "recovery_id": "reference"})
    assert data['message_key'] == 'err_settings_invalid'
    assert data['detail'] == 'literal | detail'
    assert data['message'] == '' and data['field'] == 'DOCUMENT_RANGE'
    assert data['saved'] == reports and data['recovery_id'] == 'reference'
    assert data['path'] == '' and data['args'] == {} and data['refs'] == {}


def test_ui_message_routes_share_the_envelope(api):
    client = api().test_client()
    replies = [client.post('/api/preview/file', json={'filepath': ''}, headers=AUTH),
               client.post('/api/export/logs', headers=AUTH),
               client.post('/api/settings/open_file', json={'target': 'backup'}, headers=AUTH)]
    for reply in replies:
        data = reply.get_json()
        assert {'status', 'message_key', 'detail', 'field', 'path', 'args', 'refs'} <= data.keys()
        assert data['message_key']
        assert not data['message'].startswith('i18n:')



def test_unconfirmed_export_response_keeps_source_without_empty_inventory(api, tmp_path, monkeypatch):
    import data_exporter

    seed_export_run(tmp_path)
    client = api().test_client()
    original = data_exporter.SQLiteDataExporter.export_all_formats

    def lose_result(self, destination):
        original(self, destination)
        raise RuntimeError("Lost completed export result")

    monkeypatch.setattr(data_exporter.SQLiteDataExporter, "export_all_formats", lose_result)
    response = client.post("/api/export/database", headers=AUTH)
    result = response.get_json()
    assert response.status_code == 500
    assert result["export_state"] == "unknown"
    assert result["detail"] == "Lost completed export result"
    assert "saved" not in result and "failed" not in result
    assert result["recovery_id"]
    assert list((tmp_path / "output/exports").glob("*.xlsx"))



def test_exception_envelope_preserves_localizable_reason_and_literal_fallback():
    from schemas import ConfigurationError, exception_message, message_envelope
    error = ConfigurationError("i18n:err_resume_ai_work_changed|literal | fallback",
                               setting_field="START_OVER", detail_key="err_resume_ai_context_changed_detail")
    envelope = exception_message(error)
    assert envelope["message_key"] == "err_resume_ai_work_changed"
    assert envelope["detail"] == "literal | fallback"
    assert envelope["detail_key"] == "err_resume_ai_context_changed_detail"
    assert envelope["field"] == "START_OVER" and envelope["path"] == ""
    assert message_envelope(envelope) == envelope
    old = exception_message(ValueError("literal provider diagnostic"))
    assert old["message"] == "literal provider diagnostic"
    assert "detail_key" not in old


@pytest.mark.parametrize("stale", ["old-run", ""])
def test_stop_requires_current_run_identity_and_cannot_stop_a_new_worker(api, fake_core, stale):
    client = api().test_client()
    first = start_run(client).get_json()["run_state"]
    first_abort = exec_api.run_controller.abort
    assert client.post("/api/process/stop", headers={**AUTH, "X-Run-Id": first["run_id"]}).status_code == 200
    fake_core.release.set()
    assert exec_api.run_controller.thread is not None
    exec_api.run_controller.thread.join(5)
    fake_core.release = threading.Event()
    second = start_run(client).get_json()["run_state"]
    assert second["run_id"] != first["run_id"]
    assert first_abort.is_set() and first_abort is not exec_api.run_controller.abort
    identity = first["run_id"] if stale else ""
    response = client.post("/api/process/stop", headers={**AUTH, "X-Run-Id": identity})
    assert response.status_code == 409
    assert not exec_api.run_controller.abort.is_set()
    assert response.get_json()["run_state"]["run_id"] == second["run_id"]
    response = client.post("/api/process/stop", headers={**AUTH, "X-Run-Id": second["run_id"]})
    assert response.status_code == 200 and exec_api.run_controller.abort.is_set()


@pytest.mark.parametrize("outcome", ["done", "aborted", "failed"])
def test_status_retains_terminal_outcome_after_worker_exit_and_rejects_old_events(api, monkeypatch, outcome):
    class Core:
        def __init__(self, settings, abort, on_progress):
            self.emit = on_progress

        def run(self):
            self.emit({"type": "progress", "value": 37})
            self.emit({"type": outcome, "detail": "retained diagnostic"})
    monkeypatch.setattr(exec_api, "ProcessorCore", Core)
    client = api().test_client()
    start_run(client)
    assert exec_api.run_controller.thread is not None
    exec_api.run_controller.thread.join(5)
    state = client.get("/api/process/status", headers=AUTH).get_json()
    assert state["phase"] == outcome and state["progress"] == 37
    assert not state["is_running"] and not state["is_stopping"]
    assert state["terminal"] == {"type": outcome, "detail": "retained diagnostic"}
    exec_api.run_controller.emit(state["run_id"], {"type": "progress", "value": 99})
    exec_api.run_controller.emit("older-run", {"type": "failed"})
    assert client.get("/api/process/status", headers=AUTH).get_json() == state


def test_stop_and_start_ownership_use_the_same_lock(api, fake_core):
    client = api().test_client()
    current = start_run(client).get_json()["run_state"]["run_id"]
    entered = threading.Event()
    responses = []

    def stop():
        entered.set()
        responses.append(client.post("/api/process/stop", headers={**AUTH, "X-Run-Id": current}).status_code)
    with exec_api.run_controller.lock:
        thread = threading.Thread(target=stop)
        thread.start()
        assert entered.wait(2)
        assert not exec_api.run_controller.abort.is_set()
        exec_api.run_controller.run_id = "new-owner"
    thread.join(5)
    assert not thread.is_alive() and responses == [409]
    assert not exec_api.run_controller.abort.is_set()


@pytest.mark.parametrize("outcome", ["done", "aborted", "failed"])
def test_start_is_admitted_as_soon_as_the_terminal_outcome_is_published(api, monkeypatch, outcome):
    import queue
    import time
    lifecycle: list[str] = []

    class Core:
        def __init__(self, settings, abort, on_progress):
            self.emit = on_progress

        def run(self):
            self.emit({"type": "progress", "value": 50})
            self.emit({"type": outcome})
            time.sleep(0.3)
            lifecycle.append("core shut down")
    monkeypatch.setattr(exec_api, "ProcessorCore", Core)
    client = api().test_client()
    listener: queue.Queue = queue.Queue()
    exec_api.global_broadcaster.add_listener(listener)
    try:
        first = start_run(client).get_json()["run_state"]["run_id"]
        while True:
            event = listener.get(timeout=5)
            if event.get("type") == outcome:
                break
        assert lifecycle == ["core shut down"]
        assert event["run_state"]["run_id"] == first and not event["run_state"]["is_running"]
        second = start_run(client).get_json()["run_state"]
        assert second["run_id"] != first and second["is_running"]
    finally:
        exec_api.global_broadcaster.remove_listener(listener)
        thread = exec_api.run_controller.thread
        if thread is not None:
            thread.join(5)
