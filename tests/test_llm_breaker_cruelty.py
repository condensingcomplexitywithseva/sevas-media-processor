# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from schemas import AnswerRow, RequestOutcome

from test_llm_content_breaker import CLEAN, CONTENT_FAIL, NETWORK_FAIL, run_files

TEN_CONTENT_REQUESTS = [
    RequestOutcome(number, (number,), "invalid_json_answer", "not json", "")
    for number in range(1, 11)
]

KEPT_ROWS_CONTENT_FAIL = [RequestOutcome(
    1, (1, 2, 3), "invalid_json_answer", '[{"page": 9, "answer": "a"}]', "",
    answer_rows=(
        AnswerRow(None, "9", "hallucinated_page_number", '{"answer": "a"}'),
        AnswerRow(1, "", "no_row_returned", ""),
    ),
)]


def test_content_breaker_counts_files_not_requests(tmp_path):
    first = tmp_path / "survives"
    first.mkdir()
    llm, _, error = run_files(first, [TEN_CONTENT_REQUESTS, CLEAN], threshold=2)
    assert error is None
    assert llm.calls == 2

    second = tmp_path / "trips"
    second.mkdir()
    llm, _, error = run_files(second, [TEN_CONTENT_REQUESTS, CONTENT_FAIL, CLEAN],
                              threshold=2)
    assert error is not None and "valid JSON" in str(error)
    assert llm.calls == 2


def test_a_content_file_neither_resets_nor_feeds_the_network_counter(tmp_path):
    llm, _, error = run_files(
        tmp_path, [NETWORK_FAIL, CONTENT_FAIL, NETWORK_FAIL], threshold=2)
    assert error is not None
    assert "no successful reply in between" in str(error)
    assert "valid JSON" not in str(error)
    assert llm.calls == 3


def test_network_halt_line_quotes_the_last_recorded_error(tmp_path):
    _, _, error = run_files(tmp_path, [NETWORK_FAIL, NETWORK_FAIL], threshold=2)
    assert error is not None
    assert "no successful reply in between. Last error: boom" in str(error)


def test_network_halt_line_caps_a_long_server_body(tmp_path):
    from schemas import RequestOutcome
    from batch_orchestrator import LAST_ERROR_CHARS
    long_body = "HTTP 413 rejected by the server | Server Response Body: " + "x" * 5000
    loud = [RequestOutcome(1, (1,), "network_failure", "", long_body)]
    _, _, error = run_files(tmp_path, [loud, loud], threshold=2)
    assert error is not None
    quoted = str(error).split("Last error: ", 1)[1]
    assert quoted.startswith("HTTP 413 rejected by the server")
    assert len(quoted) == LAST_ERROR_CHARS + len("...")


def test_five_413_refusals_trip_the_breaker_with_413_on_the_halt_line(tmp_path, monkeypatch, caplog):
    import logging

    import central_logger
    from fake_llm import make_server
    from test_llm_run_cruelty import make_png, point_tokens_at_the_fake, run_core, settings_for

    monkeypatch.setattr(central_logger, "setup_logging", lambda *a, **k: None)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for index in range(5):
        make_png(input_dir / f"photo_{index}.png")
    point_tokens_at_the_fake(monkeypatch, "openai")
    server = make_server("openai").start()
    try:
        for _ in range(5):
            server.queue(status=413, json={"error": {
                "message": "Request too large", "type": "invalid_request_error"}})
        settings = settings_for((input_dir, tmp_path / "output"), "openai", server.base_url,
                                LLM_OUTPUT_COLUMNS="answer", MAX_CONSECUTIVE_LLM_FAILURES=5)
        with caplog.at_level(logging.ERROR):
            events, _ = run_core(settings)
        assert len(server.requests) == 5, "one request per file, never a retry on 413"
    finally:
        server.stop()
    assert events[-1]["type"] != "done"
    halts = [r.getMessage() for r in caplog.records if "CIRCUIT BREAKER" in r.getMessage()]
    assert halts, [r.getMessage() for r in caplog.records]
    assert "HTTP 413" in halts[-1] and "Request too large" in halts[-1]


def test_kept_rows_do_not_soften_a_content_failure(tmp_path):
    llm, db, error = run_files(
        tmp_path, [KEPT_ROWS_CONTENT_FAIL, KEPT_ROWS_CONTENT_FAIL, CLEAN],
        threshold=2)
    assert error is not None and "valid JSON" in str(error)
    assert llm.calls == 2
    assert all(outcomes[0].answer_rows for _, outcomes in db.llm_requests)
