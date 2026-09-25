# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import csv
import json
import re
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from PIL import Image

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import central_logger
import llm_client
from app_context import ProcessorCore
from config_validator import Settings, _default_provider_configs
RESULTS_REPORT_COLUMNS = ["file_id", "file_path", "file_result", "notes"]

from fake_llm import ContentPolicy, make_server
from fake_llm.generic import openai_reply
from fake_llm.harness import KEY_SHAPED_TOKENS

av = pytest.importorskip("av")
np = pytest.importorskip("numpy")

DIALECT_PRESETS = ["openai", "claude", "gemini", "deepseek", "mistral",
                   "zai", "ollama", "lm-studio"]
HMS = re.compile(r"^\d{2,}:\d{2}:\d{2}\.\d{2}$")



@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(central_logger, "setup_logging", lambda *a, **k: None)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    return input_dir, tmp_path / "output"


def make_png(path):
    Image.new("RGB", (32, 32), (200, 30, 30)).save(path, "PNG")
    return path


def make_tiff(path, page_count=5):
    frames = [Image.new("RGB", (32, 32), (i * 40, 90, 200 - i * 30))
              for i in range(page_count)]
    frames[0].save(path, "TIFF", save_all=True, append_images=frames[1:])
    return path


def make_video(path, total_frames=20, fps=10):
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = 64, 64
        stream.pix_fmt = "yuv420p"
        for i in range(total_frames):
            pixels = np.zeros((64, 64, 3), dtype=np.uint8)
            pixels[:, :] = ((i * 12) % 256, (255 - i * 12) % 256, 40)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def point_tokens_at_the_fake(monkeypatch, provider):
    token = KEY_SHAPED_TOKENS.get(provider)
    env_key = f"{provider.upper().replace('-', '_')}_TOKEN"
    monkeypatch.setattr(llm_client, "get_env_tokens",
                        lambda: {env_key: token} if token else {})


def settings_for(sandbox, provider, base_url, **overrides) -> Settings:
    input_dir, output_dir = sandbox
    preset = _default_provider_configs()[provider]
    path = urlparse(preset.url).path or "/"
    values: dict[str, Any] = {
        "INPUT_FOLDER_PATH": str(input_dir),
        "OUTPUT_FOLDER_PATH": str(output_dir),
        "ENABLE_LLM_INFERENCE": True,
        "LLM_PROVIDER": provider,
        "LLM_PROVIDERS": {provider: {"url": base_url.rstrip("/") + path}},
        "LLM_MAX_RETRIES": 1,
        "LLM_RETRY_SLEEP_SECONDS": 0,
        "LLM_TIMEOUT_SECONDS": 15,
        "HALT_ON_LLM_PARSE_ERROR": False,
        "LLM_JSON_MAX_ATTEMPTS": 1,
        "LLM_ABORT_ON_MALFORMED_JSON": False,
        "MAX_JPEGS_PER_INFERENCE": 4,
        "VIDEO_MODE": "SUMMARY",
        "VIDEO_SUMMARY_TARGET_TOTAL_FRAMES": 5,
        "VIDEO_SUMMARY_SCENE_SENSITIVITY": 0.0,
        "START_OVER": False,
    }
    values.update(overrides)
    return Settings(**values)


def run_core(settings, abort_flag=None):
    events = []
    core = ProcessorCore(settings, abort_flag or threading.Event(),
                         on_progress=events.append)
    core.run()
    return events, settings.TECH_FOLDER_PATH / "application_state.db"


def sql(db_path, statement):
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(statement).fetchall()
    finally:
        connection.close()


def exported(tech_folder, prefix) -> Path | None:
    matches = sorted(tech_folder.glob(f"{prefix}_*"))
    return matches[-1] if matches else None


def exported_or_fail(tech_folder, prefix) -> Path:
    path = exported(tech_folder, prefix)
    assert path is not None, f"no {prefix} report was written"
    return path


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def sheet_rows(tech_folder, title) -> list[list[Any]]:
    import openpyxl
    workbook = openpyxl.load_workbook(exported_or_fail(tech_folder, "database_export"))
    return [list(row) for row in workbook[title].iter_rows(values_only=True)]


class HonestModel(ContentPolicy):

    def __init__(self, mode):
        super().__init__()
        self.mode = mode

    def answer(self, body=None):
        blob = json.dumps(body)
        sent = [int(p) for p in re.findall(r"Image \d+ of \d+ - page (\d+)", blob)]
        assert sent, "no labels in the request - the honest model is blind"
        if self.mode == "table_per_page":
            return json.dumps([
                {"page": p, "genre": f"genre-p{p}", "n": p,
                 "timestamp": "99:99:99.99"}
                for p in sent])
        return json.dumps({"genre": f"genre-pages-{sent[0]}-{sent[-1]}",
                           "n": len(sent)})



def test_per_page_run_takes_timestamps_from_the_page_log(sandbox, monkeypatch):
    input_dir, _ = sandbox
    make_tiff(input_dir / "a_doc.tif")
    make_png(input_dir / "b_photo.png")
    make_video(input_dir / "c_clip.mp4")
    point_tokens_at_the_fake(monkeypatch, "openai")

    srv = make_server("openai").start()
    try:
        srv.content = HonestModel("table_per_page")
        settings = settings_for(sandbox, "openai", srv.base_url,
                                LLM_OUTPUT_MODE="table_per_page",
                                LLM_OUTPUT_COLUMNS="genre,n")
        events, db_path = run_core(settings)
        request_count = len(srv.requests)
    finally:
        srv.stop()

    assert events[-1] == {"type": "done"}, events[-3:]
    pages_per_file = dict(sql(db_path,
                              "SELECT file_id, COUNT(*) FROM page_log "
                              "WHERE page_to_jpeg_status = 'ok' GROUP BY file_id"))
    assert pages_per_file[1] == 5 and pages_per_file[2] == 1
    assert 3 <= pages_per_file[3] <= 5
    expected_requests = sum(-(-count // 4) for count in pages_per_file.values())
    assert request_count == expected_requests, (request_count, pages_per_file)
    assert sql(db_path, "SELECT DISTINCT request_status FROM llm_requests") == [("ok",)]
    stored = sql(db_path,
                 "SELECT a.llm_error, p.page_number, p.video_frame_timestamp, "
                 "a.values_json FROM llm_answers a "
                 "LEFT JOIN page_log p ON p.page_id = a.page_id")
    assert len(stored) == sum(pages_per_file.values())
    assert {row[0] for row in stored} == {""}
    for _, page_number, _timestamp, values_json in stored:
        assert page_number is not None
        values = json.loads(values_json)
        assert values == {"genre": f"genre-p{page_number}", "n": page_number}
    video_stamps = [row[2] for row in stored if row[2]]
    assert len(video_stamps) == pages_per_file[3]
    assert all(HMS.match(stamp) for stamp in video_stamps), video_stamps

    tech = settings.TECH_FOLDER_PATH
    joined = sheet_rows(tech, "Results")
    index = dict(zip(joined[0], range(len(joined[0])), strict=True))
    assert "llm_timestamp" not in joined[0]
    for row in joined[1:]:
        assert row[index["llm_genre"]] == f"genre-p{row[index['pages']]}"
        assert str(row[index["llm_n"]]) == str(row[index["pages"]])
    page_rows = sheet_rows(tech, "Page Log")
    stamp_index = page_rows[0].index("video_frame_timestamp")
    joined_stamps = sorted(str(r[stamp_index]) for r in page_rows[1:] if r[stamp_index])
    assert joined_stamps == sorted(video_stamps)

    for report in ("llm_answers", "page_log", "file_registry"):
        text = exported_or_fail(tech, report).read_text(encoding="utf-8")
        assert "99:99:99.99" not in text, report
    assert "99:99:99.99" in exported_or_fail(tech, "llm_requests").read_text(
        encoding="utf-8")
    assert "99:99:99.99" not in joined_stamps
    answers_csv = read_csv(exported_or_fail(tech, "llm_answers"))
    assert sorted(r["llm_genre"] for r in answers_csv) == \
        sorted(str(r[index["llm_genre"]]) for r in joined[1:])


def test_per_file_run_never_merges_requests(sandbox, monkeypatch):
    input_dir, _ = sandbox
    make_tiff(input_dir / "doc.tif")
    point_tokens_at_the_fake(monkeypatch, "openai")

    srv = make_server("openai").start()
    try:
        srv.content = HonestModel("table_per_file")
        settings = settings_for(sandbox, "openai", srv.base_url,
                                LLM_OUTPUT_MODE="table_per_file",
                                LLM_OUTPUT_COLUMNS="genre,n",
                                MAX_JPEGS_PER_INFERENCE=2)
        events, db_path = run_core(settings)
    finally:
        srv.stop()

    assert events[-1] == {"type": "done"}
    rows = sql(db_path,
               "SELECT r.request_number, a.page_id, a.values_json FROM llm_answers a "
               "JOIN llm_requests r ON r.request_id = a.request_id ORDER BY r.request_number")
    assert [row[0] for row in rows] == [1, 2, 3]
    assert all(row[1] is None for row in rows)
    assert [json.loads(row[2])["genre"] for row in rows] == [
        "genre-pages-1-2", "genre-pages-3-4", "genre-pages-5-5"]


def test_resume_after_a_content_failure_retries_the_file(sandbox, monkeypatch):
    input_dir, _ = sandbox
    make_png(input_dir / "photo.png")
    point_tokens_at_the_fake(monkeypatch, "openai")

    srv = make_server("openai", answer='{"genre": "fixed", "n": 1}').start()
    from fake_llm.generic import openai_reply
    srv.queue(json=openai_reply("garbage, not json"))
    try:
        settings = settings_for(sandbox, "openai", srv.base_url, LLM_OUTPUT_COLUMNS="genre,n")
        events, db_path = run_core(settings)
        assert events[-1] == {"type": "done"}
        assert sql(db_path, "SELECT overall_result FROM overall_result") == [("partial_fail",)]
        events, db_path = run_core(settings)
        assert len(srv.requests) == 2, "one original request and one full-file retry"
    finally:
        srv.stop()

    assert events[-1] == {"type": "done"}
    assert sql(db_path, "SELECT file_path FROM file_registry") == [
        ("photo.png",), ("photo.png",)]
    assert sorted(sql(db_path, "SELECT overall_result FROM overall_result")) == [
        ("ok",), ("partial_fail",)]
    assert sql(db_path, "SELECT values_json FROM llm_answers") == [
        ('{"genre": "fixed", "n": 1}',)]


@pytest.mark.parametrize("provider_name", DIALECT_PRESETS)
def test_per_page_happy_path_on_every_preset(sandbox, monkeypatch, provider_name):
    input_dir, _ = sandbox
    make_png(input_dir / "photo.png")
    point_tokens_at_the_fake(monkeypatch, provider_name)

    srv = make_server(provider_name,
                      answer=json.dumps([{"page": 1, "genre": "g", "n": 1}])).start()
    try:
        settings = settings_for(sandbox, provider_name, srv.base_url,
                                LLM_OUTPUT_MODE="table_per_page",
                                LLM_OUTPUT_COLUMNS="genre,n")
        events, db_path = run_core(settings)
        assert len(srv.requests) == 1
    finally:
        srv.stop()

    assert events[-1] == {"type": "done"}, (provider_name, events[-3:])
    assert sql(db_path, "SELECT overall_result FROM overall_result") == [("ok",)]
    rows = read_csv(exported_or_fail(settings.TECH_FOLDER_PATH, "llm_answers"))
    assert [(r["llm_genre"], r["llm_n"], r["llm_error"]) for r in rows] == [
        ("g", "1", "")]


def test_stop_during_a_json_retry_in_a_real_run(sandbox, monkeypatch):
    input_dir, _ = sandbox
    make_png(input_dir / "photo.png")
    point_tokens_at_the_fake(monkeypatch, "openai")

    srv = make_server("openai").start()
    try:
        srv.queue(json=openai_reply("not json"))
        srv.queue(stall=True)
        settings = settings_for(sandbox, "openai", srv.base_url,
                                LLM_OUTPUT_COLUMNS="genre,n",
                                LLM_JSON_MAX_ATTEMPTS=3,
                                LLM_TIMEOUT_SECONDS=30)
        flag = threading.Event()
        threading.Timer(1.5, flag.set).start()
        events, db_path = run_core(settings, abort_flag=flag)
    finally:
        srv.stop()

    assert events[-1] == {"type": "aborted"}, events[-3:]
    assert sql(db_path, "SELECT request_status FROM llm_requests") == [("aborted_by_user",)]
    assert exported(settings.TECH_FOLDER_PATH, "llm_requests") is not None
    assert exported(settings.TECH_FOLDER_PATH, "database_export") is not None


def test_abort_on_malformed_ends_the_run_with_the_paid_work_persisted(
        sandbox, monkeypatch):
    input_dir, _ = sandbox
    make_png(input_dir / "a.png")
    make_png(input_dir / "b.png")
    point_tokens_at_the_fake(monkeypatch, "openai")

    srv = make_server("openai", answer="never json").start()
    try:
        settings = settings_for(sandbox, "openai", srv.base_url,
                                LLM_OUTPUT_COLUMNS="genre,n",
                                LLM_ABORT_ON_MALFORMED_JSON=True)
        events, db_path = run_core(settings)
        assert len(srv.requests) == 1
    finally:
        srv.stop()

    assert events[-1] == {"type": "failed"}, events[-3:]
    assert sql(db_path, "SELECT request_status, raw_llm_answer FROM llm_requests") == [
        ("invalid_json_answer", "never json")]
    assert sql(db_path, "SELECT file_path FROM file_registry") == [("a.png",)]


def test_a_halted_run_still_writes_its_reports(sandbox, monkeypatch):
    input_dir, _ = sandbox
    make_png(input_dir / "a.png")
    point_tokens_at_the_fake(monkeypatch, "openai")

    srv = make_server("openai", answer="never json").start()
    try:
        settings = settings_for(sandbox, "openai", srv.base_url,
                                LLM_OUTPUT_COLUMNS="genre,n",
                                LLM_ABORT_ON_MALFORMED_JSON=True)
        events, _ = run_core(settings)
    finally:
        srv.stop()

    assert events[-1] == {"type": "failed"}
    assert exported(settings.TECH_FOLDER_PATH, "llm_requests") is not None, \
        "the halted run wrote no llm_requests report"
    assert exported(settings.TECH_FOLDER_PATH, "database_export") is not None


def test_ai_off_run_workbook_has_no_llm_columns(sandbox, monkeypatch):
    input_dir, output_dir = sandbox
    make_png(input_dir / "photo.png")
    settings = Settings(
        INPUT_FOLDER_PATH=input_dir, OUTPUT_FOLDER_PATH=output_dir,
        ENABLE_LLM_INFERENCE=False, LLM_OUTPUT_COLUMNS="dormant_a,dormant_b",
        START_OVER=False)
    events, _ = run_core(settings)
    assert events[-1] == {"type": "done"}

    headers = sheet_rows(settings.TECH_FOLDER_PATH, "Results")[0]
    assert headers == RESULTS_REPORT_COLUMNS, headers
