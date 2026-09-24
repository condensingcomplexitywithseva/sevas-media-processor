# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import random
import sys
import threading
from pathlib import Path

import pytest
from PIL import Image

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import llm_client
import range_parsers
from db_controller import SQLiteDatabaseController
from range_parsers import PageRangeSelector

from fake_llm import make_server
from fake_llm.generic import GenericServer, openai_reply
from fake_llm.harness import build_client, wire_provider  # noqa: F401 (fixture)

from test_results_report import (
    add_file, export_results_csv, new_name, read_csv_rows,
    request_outcome, sql, store_requests,
)

STRICT_PRESETS = ["openai", "claude", "gemini", "deepseek", "mistral",
                  "ollama", "lm-studio"]
LABEL = "Image %d of %d - page %d."


def format_page_list(pages):
    return new_name(range_parsers, "format_page_list", "one formatter for the page spelling")(pages)


def pages_of(outcome) -> tuple[int, ...]:
    pages = getattr(outcome, "pages", None)
    assert pages is not None, "RequestOutcome.pages must exist (recorded pages)"
    return tuple(pages)


def number_of(outcome) -> int:
    number = getattr(outcome, "request_number", None)
    assert number is not None, "RequestOutcome.request_number must exist (the request vocabulary)"
    return int(number)


def label_pages(recorded) -> list[int]:
    import re
    pages = []
    for message in recorded.json["messages"]:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                match = re.fullmatch(r"Image \d+ of \d+ - page (\d+)(?:, extracted at [^.]+)?\.",
                                     part.get("text", ""))
                if match:
                    pages.append(int(match.group(1)))
    return pages


def jpegs(directory: Path, count: int) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for number in range(1, count + 1):
        path = directory / f"1_page_{number}.jpg"
        Image.new("RGB", (8, 8), (number * 30, 0, 0)).save(path, "JPEG")
        paths.append(path)
    return paths


def frames(paths):
    return [(number, path, "") for number, path in enumerate(paths, start=1)]



def test_llm_requests_stores_exactly_the_ruled_columns(tmp_path):
    SQLiteDatabaseController(tmp_path / "state.db").close()
    columns = [row[1] for row in sql(tmp_path / "state.db",
                                     "PRAGMA table_info(llm_requests)")]
    assert columns == ["request_id", "file_id", "request_number", "pages",
                       "request_status", "raw_llm_answer", "llm_network_error"]
    answer_columns = [row[1] for row in sql(tmp_path / "state.db",
                                            "PRAGMA table_info(llm_answers)")]
    assert "request_id" in answer_columns and "chunk_id" not in answer_columns


def test_stored_pages_use_the_one_spelling(tmp_path):
    c = SQLiteDatabaseController(tmp_path / "state.db")
    add_file(c, 1, "doc.pdf", [(n, f"1_p{n}.jpg", "ok", "", None) for n in range(1, 8)])
    store_requests(c, 1, [
        request_outcome(request_number=1, pages=(1, 2, 3)),
        request_outcome(request_number=2, pages=(4, 5, 7), status="ok"),
        request_outcome(request_number=3, pages=(6,), status="encoding_failure", error="x"),
        request_outcome(request_number=4, pages=(), status="not_attempted", error="y"),
    ])
    c.close()
    assert sql(tmp_path / "state.db",
               "SELECT request_number, pages FROM llm_requests ORDER BY request_number") == [
        (1, "1-3"), (2, "4-5, 7"), (3, "6"), (4, "")]


def test_the_store_refuses_a_disordered_page_list_from_the_client(tmp_path):
    c = SQLiteDatabaseController(tmp_path / "state.db")
    add_file(c, 1, "doc.pdf", [(n, f"1_p{n}.jpg", "ok", "", None) for n in range(1, 4)])
    with pytest.raises(ValueError):
        store_requests(c, 1, [request_outcome(request_number=1, pages=(3, 1, 2))])
    c.close()
    assert sql(tmp_path / "state.db", "SELECT COUNT(*) FROM llm_requests") == [(0,)]



@pytest.mark.parametrize("pages, spelled", [
    ((), ""),
    ((7,), "7"),
    ((1, 2), "1-2"),
    ((1, 2, 3, 5, 8, 9), "1-3, 5, 8-9"),
    ((4, 5, 7), "4-5, 7"),
    ((100000, 100001), "100000-100001"),
])
def test_format_page_list_spelling(pages, spelled):
    assert format_page_list(pages) == spelled


@pytest.mark.parametrize("pages", [(3, 1, 2), (2, 2), (0,), (-1, 1), (1, 1, 2)])
def test_format_page_list_refuses_disorder_and_duplicates(pages):
    with pytest.raises(ValueError):
        format_page_list(pages)


def test_format_page_list_separator_carries_a_space():
    assert format_page_list((1, 3)) == "1, 3"
    assert ",5" not in format_page_list((1, 2, 3, 5))


@pytest.mark.parametrize("seed", range(25))
def test_format_page_list_round_trips_through_the_apps_own_parser(seed):
    rng = random.Random(seed)
    pages = tuple(sorted(rng.sample(range(1, 60), rng.randint(1, 20))))
    spelled = format_page_list(pages)
    indices = PageRangeSelector(spelled).calculate_indices(max(pages) + 5).indices
    assert tuple(index + 1 for index in indices) == pages



def test_plan_and_not_attempted_markers_carry_the_plans_pages(tmp_path):
    plan_requests = new_name(llm_client, "plan_requests", "the request vocabulary")
    plan = plan_requests(frames(jpegs(tmp_path, 5)), 2)
    markers = llm_client.not_attempted_outcomes(plan, 1, "why")
    assert [(number_of(m), pages_of(m), m.status) for m in markers] == [
        (2, (3, 4), "not_attempted"), (3, (5,), "not_attempted")]
    assert all(m.error == "why" for m in markers)


def test_an_all_unreadable_request_records_the_planned_pages(tmp_path):
    missing = [tmp_path / "a.jpg", tmp_path / "b.jpg"]
    server = GenericServer().start()
    try:
        client = build_client("openai", server.base_url)
        outcomes = client.execute_network_inference(frames(missing))
    finally:
        server.stop()
    assert len(server.requests) == 0
    assert [(o.status, pages_of(o)) for o in outcomes] == [("encoding_failure", (1, 2))]


def test_a_skipped_image_is_absent_from_the_recorded_pages(tmp_path):
    paths = jpegs(tmp_path, 3)
    paths[1].unlink()
    server = GenericServer().start()
    try:
        client = build_client("openai", server.base_url)
        outcomes = client.execute_network_inference(frames(paths))
    finally:
        server.stop()
    assert [(o.status, pages_of(o)) for o in outcomes] == [("ok", (1, 3))]
    assert "1_page_2.jpg" in outcomes[0].error
    assert label_pages(server.requests[0]) == [1, 3]


@pytest.mark.parametrize("provider", STRICT_PRESETS)
def test_recorded_pages_equal_the_labels_on_the_wire(wire_provider, tmp_path, provider):  # noqa: F811 (fixture)
    srv, client = wire_provider(provider, MAX_JPEGS_PER_INFERENCE=2)
    outcomes = client.execute_network_inference(frames(jpegs(tmp_path, 5)))
    assert [o.status for o in outcomes] == ["ok"] * 3
    assert [pages_of(o) for o in outcomes] == [(1, 2), (3, 4), (5,)]
    assert [label_pages(recorded) for recorded in srv.requests] == [
        list(pages_of(o)) for o in outcomes]


def test_abort_during_the_first_request_records_the_plans_pages_for_the_rest(tmp_path):
    flag = threading.Event()
    server = GenericServer().start()
    try:
        server.queue(on_request=lambda rec: flag.set(), json=openai_reply('{"answer": "a"}'))
        client = build_client("openai", server.base_url, MAX_JPEGS_PER_INFERENCE=2)
        outcomes = client.execute_network_inference(frames(jpegs(tmp_path, 5)), abort_flag=flag)
    finally:
        server.stop()
    assert len(server.requests) == 1
    assert [(number_of(o), pages_of(o), o.status) for o in outcomes[1:]] == [
        (2, (3, 4), "not_attempted"), (3, (5,), "not_attempted")]
    assert pages_of(outcomes[0]) == (1, 2)


def test_every_corrective_attempt_carries_the_same_pages(tmp_path):
    server = make_server("openai").start()
    try:
        server.queue(json=openai_reply("prose, sorry"))
        server.queue(json=openai_reply('{"answer": "fine"}'))
        client = build_client("openai", server.base_url, LLM_JSON_MAX_ATTEMPTS=3)
        outcomes = client.execute_network_inference(frames(jpegs(tmp_path, 3)))
    finally:
        server.stop()
    assert len(server.requests) == 2
    assert [label_pages(r) for r in server.requests] == [[1, 2, 3], [1, 2, 3]]
    assert outcomes[0].status == "ok" and pages_of(outcomes[0]) == (1, 2, 3)
    assert "attempt 1" in outcomes[0].error



def test_encode_skip_end_to_end_through_the_real_app(tmp_path, monkeypatch, caplog):
    from test_llm_run_cruelty import (make_tiff, point_tokens_at_the_fake,
                                      run_core, settings_for)
    import central_logger
    monkeypatch.setattr(central_logger, "setup_logging", lambda *a, **k: None)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_tiff(input_dir / "doc.tif", page_count=3)
    point_tokens_at_the_fake(monkeypatch, "openai")

    original = SQLiteDatabaseController.get_successful_frames

    def frames_then_lose_page_two(self, file_id, base_output_folder):
        result = original(self, file_id, base_output_folder)
        for page_number, path, _ in result:
            if page_number == 2:
                path.unlink()
        return result

    monkeypatch.setattr(SQLiteDatabaseController, "get_successful_frames",
                        frames_then_lose_page_two)

    server = make_server("openai", answer='{"answer": "seen"}').start()
    try:
        settings = settings_for((input_dir, tmp_path / "output"), "openai", server.base_url,
                                LLM_OUTPUT_COLUMNS="answer")
        with caplog.at_level("INFO"):
            events, db_path = run_core(settings)
        assert len(server.requests) == 1
        assert label_pages(server.requests[0]) == [1, 3]
    finally:
        server.stop()

    assert events[-1] == {"type": "done"}, events[-3:]
    stored = sql(db_path, "SELECT pages, request_status, llm_network_error FROM llm_requests")
    assert len(stored) == 1
    assert stored[0][0] == "1, 3" and stored[0][1] == "ok"
    assert "page_2" in stored[0][2] and "Base64 Encoding Failure" in stored[0][2]

    _, rows = read_csv_rows(export_results_csv(db_path, tmp_path / "reports"))
    assert len(rows) == 1
    answer = rows[0]
    assert answer["file_result"] == "ok"
    assert answer["pages"] == "1, 3" and "page_2" in answer["notes"]
    assert answer["llm_answer"] == "seen"

    said_chunk = [r.getMessage() for r in caplog.records if "chunk" in r.getMessage().lower()]
    assert said_chunk == []
