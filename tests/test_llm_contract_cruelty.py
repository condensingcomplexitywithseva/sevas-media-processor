# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from schemas import ConfigurationError

from fake_llm import make_server
from fake_llm.generic import openai_reply
from fake_llm.harness import build_client
from test_llm_json_ladder import (
    DECLARED_COLUMNS,
    VALID,
    VALID_ROWS,
    assistant_messages,
    content_texts,
    first_user_blob,
    flag_after,
    make_json_client,
    messages_of,
    one_request,
    pages,
    rows_of,
    run_reply,
)
from fake_llm.generic import GenericServer

DIALECT_PRESETS = ["openai", "claude", "gemini", "deepseek", "mistral",
                   "zai", "ollama", "lm-studio"]



@pytest.fixture
def server():
    srv = GenericServer().start()
    yield srv
    srv.stop()


@pytest.fixture
def make_jpegs(tmp_path):
    def _make(n, stem="frame"):
        paths = []
        for i in range(n):
            p = tmp_path / f"{stem}_{i}.jpg"
            Image.new("RGB", (8, 8), (i * 30 % 256, 0, 0)).save(p, "JPEG")
            paths.append(p)
        return paths
    return _make


def _reject_constant(name):
    raise ValueError(f"{name} is not valid JSON")


def strict_json(text):
    return json.loads(text, parse_constant=_reject_constant)


def user_message(recorded):
    return next(m for m in messages_of(recorded) if m["role"] == "user")


def assistant_text(recorded):
    assistants = assistant_messages(recorded)
    assert len(assistants) == 1
    return "\n".join(content_texts(assistants[0]))



@pytest.mark.parametrize("mode", ["table_per_file", "table_per_page"])
@pytest.mark.parametrize("provider_name", DIALECT_PRESETS)
def test_message_part_order_on_every_preset(provider_name, mode, make_jpegs):
    srv = make_server(provider_name).start()
    try:
        client = build_client(
            provider_name, srv.base_url,
            LLM_OUTPUT_MODE=mode,
            LLM_OUTPUT_COLUMNS=DECLARED_COLUMNS,
            LLM_OUTPUT_COLUMN_INSTRUCTIONS="",
            LLM_JSON_MAX_ATTEMPTS=1,
            LLM_ABORT_ON_MALFORMED_JSON=False,
        )
        client.execute_network_inference(pages(make_jpegs(2)))
    finally:
        srv.stop()

    parts = user_message(srv.requests[0])["content"]
    kinds = ["image" if p["type"] in ("image", "image_url") else "text"
             for p in parts]
    assert kinds == ["text", "text", "text", "image", "text", "image", "text"], \
        (provider_name, kinds)
    texts = [p.get("text", "") for p in parts]
    assert texts[0] == client.user_prompt_string
    assert texts[1] == "This request contains images 1-2 of 2 from one file."
    assert texts[2].startswith("Image 1 of 2 - page 1")
    assert texts[4].startswith("Image 2 of 2 - page 2")
    contract = texts[6]
    assert contract.startswith("Return ONLY")
    assert sum(1 for t in texts if "Return ONLY" in t) == 1
    if mode == "table_per_page":
        assert '"page"' in contract
    else:
        assert '"page"' not in contract


def test_request_body_never_carries_a_name_or_path(server, make_jpegs, tmp_path):
    secret_dir = tmp_path / "HIDDEN_CLIENT_ROOT"
    secret_dir.mkdir()
    paths = []
    for i in range(3):
        p = secret_dir / f"SECRET_STEM_{i}.jpg"
        Image.new("RGB", (8, 8), (i * 40, 0, 0)).save(p, "JPEG")
        paths.append(p)
    server.queue(json=openai_reply(VALID))
    client = make_json_client(server)
    outcome = one_request(client, pages(paths))
    assert outcome.status == "ok"

    body = server.requests[0].text
    for needle in ("SECRET_STEM", "HIDDEN_CLIENT_ROOT", tmp_path.name,
                   str(tmp_path)):
        assert needle not in body, needle


def test_replayed_failed_answer_survives_hostile_text(server, make_jpegs):
    hostile_wire = ('{"choices":[{"message":{"role":"assistant",'
                    '"content":"bad \\ud800 \\u0000 \\u001b[31m text"},'
                    '"finish_reason":"stop"}]}')
    server.queue(raw=hostile_wire, content_type="application/json")
    server.queue(json=openai_reply(VALID))
    client = make_json_client(server, LLM_JSON_MAX_ATTEMPTS=2)
    outcome = one_request(client, pages(make_jpegs(3)))

    assert outcome.status == "ok", outcome.error
    assert len(server.requests) == 2
    assert server.requests[1].json_ok, "the corrective request was not JSON"
    assert "bad" in assistant_text(server.requests[1])


def test_replayed_failed_answer_is_capped(server, make_jpegs):
    flood = "x" * 3_000_000
    server.queue(json=openai_reply(flood))
    server.queue(json=openai_reply(VALID))
    client = make_json_client(server, LLM_JSON_MAX_ATTEMPTS=2)
    outcome = one_request(client, pages(make_jpegs(3)))

    assert outcome.status == "ok"
    replayed = assistant_text(server.requests[1])
    assert len(replayed) <= 64 * 1024 + 256, len(replayed)
    assert replayed.startswith("x" * 1000)


def test_labels_stay_global_across_json_retries(server, make_jpegs):
    server.queue(json=openai_reply(VALID))
    server.queue(json=openai_reply("bad"))
    server.queue(json=openai_reply(json.dumps([
        {"page": 4, "genre": "g4", "answer": "a4"},
        {"page": 5, "genre": "g5", "answer": "a5"},
    ])))
    client = make_json_client(server, MAX_JPEGS_PER_INFERENCE=3,
                              LLM_JSON_MAX_ATTEMPTS=2)
    outcomes = client.execute_network_inference(pages(make_jpegs(5)))
    assert [o.status for o in outcomes] == ["ok", "ok"]
    assert len(server.requests) == 3

    for recorded in server.requests[1:]:
        blob = first_user_blob(recorded)
        assert "images 4-5 of 5" in blob
        assert "Image 4 of 5 - page 4" in blob
        assert "Image 1 of 5" not in blob



@pytest.mark.parametrize("wrapper", [
    pytest.param("```JSON\n{}\n```", id="uppercase-tag"),
    pytest.param("    ```json\n    {}\n    ```", id="indented-fence"),
    pytest.param("```json\n{}", id="no-closing-fence"),
    pytest.param("```json\r\n{}\r\n```\r\n", id="crlf"),
    pytest.param("\u200b{}", id="zero-width-space"),
    pytest.param("\xa0{}\xa0", id="nbsp"),
    pytest.param("// the answer follows\n{}", id="line-comment-before"),
    pytest.param("{}\n\x00", id="nul-after"),
])
def test_more_fence_and_invisible_wrappers_all_open(server, make_jpegs, wrapper):
    outcome = run_reply(server, make_jpegs, wrapper.format(VALID))
    assert outcome.status == "ok", (wrapper, outcome.error)
    assert [row.page_number for row in rows_of(outcome)] == [1, 2, 3]


@pytest.mark.parametrize("reply", [
    pytest.param(VALID + "\n\nNote: brackets like [these] are fine.",
                 id="trailing-prose-bracket"),
    pytest.param(VALID + "\n(3 items, in [square] brackets)",
                 id="trailing-parenthetical"),
])
def test_prose_after_the_json_with_a_stray_bracket_still_parses(
        server, make_jpegs, reply):
    outcome = run_reply(server, make_jpegs, reply)
    assert outcome.status == "ok", outcome.error
    assert [row.page_number for row in rows_of(outcome)] == [1, 2, 3]


@pytest.mark.parametrize("reply", [
    pytest.param('{"genre": "g", "answer": "a"}\n\nTip: use {braces} for objects.',
                 id="trailing-prose-brace"),
    pytest.param('Sure {here} it is:\n{"genre": "g", "answer": "a"}',
                 id="leading-prose-brace"),
])
def test_per_file_prose_with_a_stray_brace_still_parses(server, make_jpegs, reply):
    server.queue(json=openai_reply(reply))
    client = make_json_client(server, LLM_OUTPUT_MODE="table_per_file")
    outcome = one_request(client, pages(make_jpegs(3)))
    assert outcome.status == "ok", outcome.error
    assert json.loads(rows_of(outcome)[0].values_json) == {
        "genre": "g", "answer": "a"}


@pytest.mark.parametrize("reply", [
    pytest.param("{}", id="empty-object-in-per-page"),
    pytest.param("[{}]", id="array-of-empty-object"),
    pytest.param("[null]", id="array-of-null"),
    pytest.param("[[]]", id="nested-empty-array"),
    pytest.param("null", id="bare-null"),
    pytest.param('[{page: 1, genre: "g", answer: "a"}]', id="unquoted-keys"),
    pytest.param('[{"page": 1, "genre": undefined, "answer": "a"}]',
                 id="undefined-value"),
    pytest.param('[{“page”: 1, “genre”: “g”, '
                 '“answer”: “a”}]', id="smart-quotes"),
    pytest.param('[{"page": 1, "genre": "g", "answer": "a"} /* one */]',
                 id="block-comment-inside"),
    pytest.param('[{"page": 1, "Genre": "g", "answer": "a"}]',
                 id="key-case-mismatch"),
])
def test_more_malformed_shapes_fail_the_request_with_no_rows(server, make_jpegs, reply):
    outcome = run_reply(server, make_jpegs, reply)
    assert outcome.status == "invalid_json_answer", (reply, outcome)
    assert rows_of(outcome) == []
    assert outcome.raw_answer == reply


@pytest.mark.parametrize("blank", [
    pytest.param("   \n\t  ", id="whitespace-only"),
])
def test_whitespace_only_reply_is_an_empty_reply(server, make_jpegs, blank):
    outcome = run_reply(server, make_jpegs, blank)
    assert outcome.status == "provider_reply_parse_error"


@pytest.mark.parametrize("blank", [
    pytest.param("\ufeff", id="bom-only"),
    pytest.param("\u200b", id="zero-width-space-only"),
    pytest.param("\ufeff \n", id="bom-and-whitespace"),
])
def test_invisible_only_replies_are_empty_replies(server, make_jpegs, blank):
    outcome = run_reply(server, make_jpegs, blank)
    assert outcome.status == "provider_reply_parse_error", outcome


def test_fence_only_reply_is_malformed_text(server, make_jpegs):
    outcome = run_reply(server, make_jpegs, "```json\n```")
    assert outcome.status == "invalid_json_answer"


@pytest.mark.parametrize("number", ["1e400", "-1e400", "1E999"])
def test_float_overflow_never_reaches_storage_as_infinity(server, make_jpegs, number):
    reply = json.dumps(VALID_ROWS).replace('"g1"', number, 1)
    assert number in reply
    outcome = run_reply(server, make_jpegs, reply)

    assert outcome.status in ("ok", "invalid_json_answer"), outcome
    for row in rows_of(outcome):
        if row.values_json:
            assert "Infinity" not in row.values_json, row.values_json
            strict_json(row.values_json)


def test_twenty_megabyte_answer_is_a_verdict_not_a_hang(server, make_jpegs):
    big = "y" * 20_000_000
    payload = [dict(VALID_ROWS[0], answer=big), *VALID_ROWS[1:]]
    started = time.monotonic()
    outcome = run_reply(server, make_jpegs, json.dumps(payload))
    elapsed = time.monotonic() - started

    assert elapsed < 60, f"{elapsed:.1f}s for a 20 MB reply"
    assert outcome.status == "ok", outcome.error
    first = next(r for r in rows_of(outcome) if r.page_number == 1)
    assert len(first.values_json) > 20_000_000


def test_two_hundred_thousand_rows_for_three_images_complete(server, make_jpegs):
    rows = [{"page": (i % 3) + 1, "genre": "g", "answer": "a"}
            for i in range(200_000)]
    started = time.monotonic()
    outcome = run_reply(server, make_jpegs, json.dumps(rows))
    elapsed = time.monotonic() - started

    assert elapsed < 60, f"{elapsed:.1f}s for 200k rows"
    assert outcome.status in ("ok", "invalid_json_answer"), outcome.status
    assert len(rows_of(outcome)) == 200_000


@pytest.mark.parametrize("reply", [
    pytest.param("[" * 100_000 + "]" * 100_000, id="nested-100k"),
    pytest.param("[" * 100_000, id="unbalanced-100k"),
    pytest.param("{x} " * 50_000 + VALID, id="50k-stray-braces-then-json"),
])
def test_a_reply_of_brackets_is_judged_in_bounded_time(server, make_jpegs, reply):
    started = time.monotonic()
    outcome = run_reply(server, make_jpegs, reply)
    elapsed = time.monotonic() - started

    assert elapsed < 3, f"{elapsed:.1f}s to judge {len(reply)} characters"
    assert outcome.status in ("ok", "invalid_json_answer"), outcome.status
    assert outcome.raw_answer == reply


def test_the_opener_bound_is_exactly_256(server, make_jpegs):
    from llm_client import _MAX_OPENERS_TRIED

    assert _MAX_OPENERS_TRIED == 256

    found = run_reply(server, make_jpegs, "{x} " * 255 + VALID)
    assert found.status == "ok", found.error
    refused = run_reply(server, make_jpegs, "{x} " * 256 + VALID)
    assert refused.status == "invalid_json_answer", refused.status
    assert "first 256" in refused.error


def test_numbers_keep_their_json_spelling(server, make_jpegs):
    reply = ('{"n": 123456789012345678901234567890, "e": 1E2, "z": -0, '
             '"f": 0.1000000000000000055511151231257827}')
    server.queue(json=openai_reply(reply))
    client = make_json_client(server, LLM_OUTPUT_MODE="table_per_file",
                              LLM_OUTPUT_COLUMNS="n,e,z,f")
    outcome = one_request(client, pages(make_jpegs(1)))
    assert outcome.status == "ok", outcome.error

    values_json = rows_of(outcome)[0].values_json
    values = strict_json(values_json)
    assert values["n"] == 123456789012345678901234567890
    assert isinstance(values["n"], int)
    assert "123456789012345678901234567890" in values_json
    assert values["e"] == 100.0 and "100.0" in values_json
    assert values["z"] == 0
    assert values["f"] == 0.1


def test_row_without_a_page_key_is_kept_as_hallucinated(server, make_jpegs):
    reply = json.dumps([{"genre": "g0", "answer": "a0"}, *VALID_ROWS[1:]])
    outcome = run_reply(server, make_jpegs, reply)
    ghost = [r for r in rows_of(outcome)
             if r.llm_error == "hallucinated_page_number"]
    assert len(ghost) == 1
    assert ghost[0].page_number is None
    assert ghost[0].raw_model_page_number == ""
    assert json.loads(ghost[0].values_json)["genre"] == "g0"
    assert [r.page_number for r in rows_of(outcome)
            if r.llm_error == "no_row_returned"] == [1]


def test_nested_values_keep_non_ascii_readable(server, make_jpegs):
    reply = json.dumps([
        {"page": 1, "genre": {"город": "Москва"},
         "answer": "a"},
        *VALID_ROWS[1:],
    ], ensure_ascii=False)
    outcome = run_reply(server, make_jpegs, reply)
    assert outcome.status == "ok", outcome.error
    first = next(r for r in rows_of(outcome) if r.page_number == 1)
    assert "Москва" in first.values_json
    assert "\\u041c" not in first.values_json



def test_network_retry_nests_inside_a_json_attempt(server, make_jpegs):
    server.queue(json=openai_reply("bad"))
    server.queue(status=500, json={"error": "hiccup"})
    server.queue(json=openai_reply(VALID))
    client = make_json_client(server, LLM_JSON_MAX_ATTEMPTS=2,
                              LLM_MAX_RETRIES=2)
    outcome = one_request(client, pages(make_jpegs(3)))

    assert outcome.status == "ok", outcome.error
    assert len(server.requests) == 3
    assert "attempt 1" in outcome.error


def test_stop_beats_an_absurd_attempt_budget(server, make_jpegs):
    server.default_behavior = {"json": openai_reply("never json")}
    client = make_json_client(server, LLM_JSON_MAX_ATTEMPTS=1_000_000)
    flag = flag_after(0.3)
    started = time.monotonic()
    outcomes = client.execute_network_inference(pages(make_jpegs(3)),
                                                abort_flag=flag)
    elapsed = time.monotonic() - started

    assert elapsed < 3, f"Stop took {elapsed:.1f}s"
    assert [o.status for o in outcomes] == ["aborted_by_user"]
    assert len(server.requests) < 1_000_000


ALL_HALLUCINATED = json.dumps([
    {"page": 7, "genre": "g7", "answer": "a7"},
    {"page": 8, "genre": "g8", "answer": "a8"},
    {"page": 9, "genre": "g9", "answer": "a9"},
])
ALL_DUPLICATED = json.dumps([
    {"page": 1, "genre": "first", "answer": "a"},
    {"page": 1, "genre": "second", "answer": "b"},
    {"page": 1, "genre": "third", "answer": "c"},
])
ONE_CLEAN_TWO_INVENTED = json.dumps([
    {"page": 1, "genre": "g1", "answer": "a1"},
    {"page": 8, "genre": "g8", "answer": "a8"},
    {"page": 9, "genre": "g9", "answer": "a9"},
])


@pytest.mark.parametrize("reply", [
    pytest.param(ALL_HALLUCINATED, id="all-hallucinated"),
    pytest.param(ALL_DUPLICATED, id="all-duplicated"),
    pytest.param(ONE_CLEAN_TWO_INVENTED, id="one-clean-two-invented"),
])
def test_kept_rows_after_exhausted_attempts_mark_the_request_invalid(
        server, make_jpegs, reply):
    outcome = run_reply(server, make_jpegs, reply)
    assert outcome.status == "invalid_json_answer", outcome.status
    assert rows_of(outcome), "the paid rows are still kept"
    assert any(r.llm_error in ("hallucinated_page_number",
                               "duplicate_page_number")
               for r in rows_of(outcome))


def test_a_missing_row_alone_keeps_the_request_ok(server, make_jpegs):
    reply = json.dumps([VALID_ROWS[0], VALID_ROWS[2]])
    outcome = run_reply(server, make_jpegs, reply)
    assert outcome.status == "ok"
    assert [r.page_number for r in rows_of(outcome)
            if r.llm_error == "no_row_returned"] == [2]


def test_abort_toggle_halts_on_kept_rows_too(server, make_jpegs):
    server.queue(json=openai_reply(ALL_HALLUCINATED))
    client = make_json_client(server, LLM_JSON_MAX_ATTEMPTS=1,
                              LLM_ABORT_ON_MALFORMED_JSON=True)
    with pytest.raises(ConfigurationError) as excinfo:
        client.execute_network_inference(pages(make_jpegs(3)))

    carried = getattr(excinfo.value, "request_outcomes", None)
    assert carried is not None
    assert carried[0].status == "invalid_json_answer"
    assert rows_of(carried[0]), "kept rows survive the halt"


def test_stop_flag_polled_between_attempts_is_honoured_before_the_next_post(
        server, make_jpegs, monkeypatch):
    server.queue(json=openai_reply("bad"))
    server.queue(json=openai_reply(VALID))
    client = make_json_client(server, LLM_JSON_MAX_ATTEMPTS=3)
    flag = threading.Event()
    judge = client._judge_answer_text
    received = []

    def judge_then_stop(text, sent_pages):
        result = judge(text, sent_pages)
        received.append(text)
        flag.set()
        return result
    monkeypatch.setattr(client, "_judge_answer_text", judge_then_stop)
    outcomes = client.execute_network_inference(pages(make_jpegs(3)), abort_flag=flag)
    assert received == ["bad"]
    assert [o.status for o in outcomes] == ["aborted_by_user"]
    assert len(server.requests) == 1, "attempt 2 was posted after Stop"
    assert flag.is_set()


def test_abort_never_leaves_a_worker_holding_the_next_request(server, make_jpegs):
    server.queue(json=openai_reply("bad"))
    server.queue(stall=True)
    client = make_json_client(server, LLM_JSON_MAX_ATTEMPTS=3,
                              LLM_TIMEOUT_SECONDS=30)
    flag = threading.Event()
    threading.Timer(0.8, flag.set).start()
    outcomes = client.execute_network_inference(pages(make_jpegs(3)),
                                                abort_flag=flag)
    assert [o.status for o in outcomes] == ["aborted_by_user"]
    seen = len(server.requests)
    time.sleep(1.5)
    assert len(server.requests) == seen, "a straggler posted after Stop"
