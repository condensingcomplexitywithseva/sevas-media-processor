# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import errno
import json
import logging
import socket
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_validator import _default_provider_configs
from llm_client import LLMClient
from schemas import ConfigurationError

from fake_llm.generic import (
    GenericServer as FakeLLMServer, claude_reply, openai_reply,
)
from fake_llm.harness import KEY_SHAPED_TOKENS

_openai_fake_token = KEY_SHAPED_TOKENS["openai"]
assert _openai_fake_token is not None
FAKE_TOKEN: str = _openai_fake_token


@pytest.fixture
def server():
    srv = FakeLLMServer().start()
    yield srv
    srv.stop()


def make_wire_client(server, provider_overrides=None,
                     token: str | None = FAKE_TOKEN,
                     **settings_overrides):
    provider = SimpleNamespace(
        url=server.url,
        model="test-model",
        system_prompt_location="messages",
        image_payload_style="data_uri",
        response_extraction_path="choices[0].message.content",
        auth_header_key="Authorization",
        auth_header_format="Bearer {token}",
        extra_header_key="",
        extra_header_value="",
        require_max_tokens=False,
        max_tokens=1000,
        max_tokens_field="max_tokens",
        reasoning_handling="preserve",
    )
    for key, value in (provider_overrides or {}).items():
        setattr(provider, key, value)

    settings = SimpleNamespace(
        ACTIVE_PROVIDER_CONFIG=provider,
        MAX_JPEGS_PER_INFERENCE=10,
        LLM_MAX_RETRIES=3,
        LLM_TIMEOUT_SECONDS=15,
        LLM_RETRY_SLEEP_SECONDS=0,
        HALT_ON_LLM_PARSE_ERROR=False,
        LLM_SYSTEM_PROMPT="system prompt",
        LLM_SYSTEM_PROMPT_MODE="TEXT",
        LLM_USER_PROMPT="user prompt",
        LLM_USER_PROMPT_MODE="TEXT",
        LLM_PROVIDER="custom",
        LLM_OUTPUT_MODE="table_per_file",
        LLM_OUTPUT_COLUMNS="answer",
        LLM_OUTPUT_COLUMN_INSTRUCTIONS="",
        LLM_JSON_MAX_ATTEMPTS=1,
        LLM_ABORT_ON_MALFORMED_JSON=False,
    )
    for key, value in settings_overrides.items():
        setattr(settings, key, value)

    return LLMClient(settings, token=token)


@pytest.fixture
def jpegs(tmp_path):
    paths = []
    for i in range(5):
        p = tmp_path / f"frame_{i}.jpg"
        Image.new("RGB", (8, 8), (i * 40, 0, 0)).save(p, "JPEG")
        paths.append(p)
    return paths


def pages(paths, timestamps=None):
    if timestamps is None:
        timestamps = [""] * len(paths)
    return [(number, path, ts)
            for number, (path, ts) in enumerate(
                zip(paths, timestamps, strict=True), start=1)]


def flag_after(seconds):
    flag = threading.Event()
    timer = threading.Timer(seconds, flag.set)
    timer.daemon = True
    timer.start()
    return flag



def test_stop_reacts_promptly_while_server_never_answers(server, jpegs):
    server.queue(stall=True)
    client = make_wire_client(server, LLM_TIMEOUT_SECONDS=30)

    started = time.perf_counter()
    outcomes = client.execute_network_inference(pages(jpegs[:1]), abort_flag=flag_after(0.3))
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0
    assert [o.status for o in outcomes] == ["aborted_by_user"]
    assert "aborted by user" in outcomes[0].error.lower()


def test_abort_mid_call_is_reported_as_abort_not_network_error(server, jpegs):
    server.queue(stall=True)
    client = make_wire_client(server, LLM_TIMEOUT_SECONDS=30)

    outcomes = client.execute_network_inference(pages(jpegs[:1]), abort_flag=flag_after(0.3))

    assert [o.status for o in outcomes] == ["aborted_by_user"]
    assert "aborted by user" in outcomes[0].error.lower()
    assert "network failure" not in outcomes[0].error.lower()



def test_abortable_sleep_interrupts_within_a_tick(server):
    client = make_wire_client(server)

    started = time.perf_counter()
    interrupted = client._abortable_sleep(10, flag_after(0.2))
    elapsed = time.perf_counter() - started

    assert interrupted is True
    assert elapsed < 1.5



def test_abort_during_first_request_prevents_all_further_requests(server, jpegs):
    server.queue(
        json=openai_reply("request one answer"),
        on_request=lambda rec: abort.set(),
    )
    abort = threading.Event()
    client = make_wire_client(server, MAX_JPEGS_PER_INFERENCE=1)

    outcomes = client.execute_network_inference(pages(jpegs[:3]), abort_flag=abort)

    assert len(server.requests) == 1
    assert [o.status for o in outcomes] == \
        ["aborted_by_user", "not_attempted", "not_attempted"]
    assert "aborted by user" in outcomes[0].error.lower()
    assert "network failure" not in outcomes[0].error.lower()
    assert [(o.request_number, o.pages) for o in outcomes] == \
        [(1, (1,)), (2, (2,)), (3, (3,))]
    assert all("Not attempted" in o.error for o in outcomes[1:])



def test_images_split_into_requests_each_carrying_the_prompt(server, jpegs):
    client = make_wire_client(server, MAX_JPEGS_PER_INFERENCE=2)

    outcomes = client.execute_network_inference(pages(jpegs))

    assert [o.status for o in outcomes] == ["ok", "ok", "ok"]
    assert [(o.request_number, o.pages) for o in outcomes] == [
        (1, (1, 2)), (2, (3, 4)), (3, (5,))]
    assert len(server.requests) == 3

    image_counts = []
    for recorded in server.requests:
        content = recorded.json["messages"][-1]["content"]
        assert content[0] == {"type": "text", "text": "user prompt"}
        images = [c for c in content if c["type"] == "image_url"]
        for entry in images:
            assert entry["image_url"]["url"].startswith("data:image/jpeg;base64,")
        image_counts.append(len(images))
    assert image_counts == [2, 2, 1]

    intros = [r.json["messages"][-1]["content"][1]["text"] for r in server.requests]
    assert intros == [
        "This request contains images 1-2 of 5 from one file.",
        "This request contains images 3-4 of 5 from one file.",
        "This request contains image 5 of 5 from one file.",
    ]
    all_labels = []
    for recorded in server.requests:
        content = recorded.json["messages"][-1]["content"]
        for i, part in enumerate(content):
            if part["type"] == "image_url":
                label = content[i - 1]
                assert label["type"] == "text"
                all_labels.append(label["text"])
    assert all_labels == [f"Image {n} of 5 - page {n}." for n in range(1, 6)]


def test_labels_carry_page_and_timestamp_but_never_the_file_name(server, jpegs):
    frames = [(2, jpegs[0], "00:00:05.00"), (5, jpegs[1], ""),
              (9, jpegs[2], "01:02:03.44")]

    client = make_wire_client(server)
    outcomes = client.execute_network_inference(frames)

    assert [o.status for o in outcomes] == ["ok"]
    content = server.requests[0].json["messages"][-1]["content"]
    texts = [c["text"] for c in content if c["type"] == "text"]
    assert texts[:5] == [
        "user prompt",
        "This request contains images 1-3 of 3 from one file.",
        "Image 1 of 3 - page 2, extracted at 00:00:05.00.",
        "Image 2 of 3 - page 5.",
        "Image 3 of 3 - page 9, extracted at 01:02:03.44.",
    ]
    assert len(texts) == 6
    assert texts[5].startswith("Return ONLY a single JSON object")
    for text in texts:
        for _, path, _ in frames:
            assert path.name not in text
            assert path.stem not in text


def test_claude_style_payload_and_response_over_the_wire(server, jpegs):
    server.default_behavior = {
        "json": claude_reply('{"answer": "a tiger on a pyramid"}')}
    client = make_wire_client(
        server,
        provider_overrides={
            "image_payload_style": "base64_dict",
            "system_prompt_location": "top_level",
            "require_max_tokens": True,
            "max_tokens": 4321,
            "reasoning_handling": "parse_claude_blocks",
        },
    )

    outcomes = client.execute_network_inference(pages(jpegs[:2]))

    assert [o.status for o in outcomes] == ["ok"]
    assert "a tiger on a pyramid" in outcomes[0].raw_answer

    payload = server.requests[0].json
    assert payload["system"] == "system prompt"
    assert payload["max_tokens"] == 4321
    images = [c for c in payload["messages"][0]["content"] if c["type"] == "image"]
    assert len(images) == 2
    for entry in images:
        assert entry["source"]["type"] == "base64"
        assert entry["source"]["media_type"] == "image/jpeg"



def test_auth_and_extra_headers_reach_the_wire(server, jpegs):
    client = make_wire_client(
        server,
        provider_overrides={
            "auth_header_key": "x-api-key",
            "auth_header_format": "{token}",
            "extra_header_key": "anthropic-version",
            "extra_header_value": "2023-06-01",
        },
    )

    client.execute_network_inference(pages(jpegs[:1]))

    headers = server.requests[0].headers
    assert headers.get("X-Api-Key") == FAKE_TOKEN
    assert headers.get("Anthropic-Version") == "2023-06-01"


def test_local_provider_without_token_sends_no_auth_header(server, jpegs, caplog):
    with caplog.at_level(logging.DEBUG):
        client = make_wire_client(
            server,
            token=None,
            LLM_PROVIDER="lm-studio",
            provider_overrides={"auth_header_key": "", "auth_header_format": ""},
        )
        outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["ok"]
    assert "Authorization" not in server.requests[0].headers
    assert "No API key detected" not in caplog.text


def test_token_never_appears_in_logs_even_across_retries(server, jpegs, caplog):
    for _ in range(3):
        server.queue(status=500, json={"error": "internal"})

    with caplog.at_level(logging.DEBUG):
        client = make_wire_client(server)
        outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["network_failure"]
    assert FAKE_TOKEN not in caplog.text



def test_happy_path_over_real_http(server, jpegs):
    server.queue(json=openai_reply('{"answer": "the cat sat on the mat"}'))
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["ok"]
    assert "the cat sat on the mat" in outcomes[0].raw_answer
    assert outcomes[0].error == ""


def test_http_401_halts_the_whole_batch(server, jpegs):
    server.queue(status=401, json={"error": "invalid key"})
    client = make_wire_client(server)

    with pytest.raises(ConfigurationError, match="AUTHENTICATION"):
        client.execute_network_inference(pages(jpegs[:1]))
    assert len(server.requests) == 1


def test_transient_500_is_retried_then_succeeds(server, jpegs):
    server.queue(status=500, json={"error": "hiccup"})
    server.queue(json=openai_reply('{"answer": "recovered fine"}'))
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["ok"]
    assert "recovered fine" in outcomes[0].raw_answer
    assert len(server.requests) == 2


def test_retry_exhaustion_records_network_failure(server, jpegs):
    for _ in range(3):
        server.queue(status=500, json={"error": "still down"})
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["network_failure"]
    assert len(server.requests) == 3
    assert "network failure" in outcomes[0].error.lower()


def test_garbage_html_body_is_retried_like_a_network_failure(server, jpegs):
    for _ in range(3):
        server.queue(raw="<html>this is not JSON</html>")
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["network_failure"]
    assert len(server.requests) == 3


def test_truncated_reply_halts_when_configured(server, jpegs):
    server.queue(json=openai_reply("half an ans", finish_reason="length"))
    client = make_wire_client(server, HALT_ON_LLM_PARSE_ERROR=True)

    with pytest.raises(ConfigurationError, match="Token Limit"):
        client.execute_network_inference(pages(jpegs[:1]))


def test_truncated_reply_records_token_limit_without_halting(server, jpegs):
    server.queue(json=openai_reply("half an ans", finish_reason="length"))
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["token_limit_exceeded"]
    assert "Token Limit Exceeded" in outcomes[0].error


def test_unresponsive_provider_times_out_instead_of_hanging(server, jpegs, caplog):
    server.queue(stall=True)
    server.queue(stall=True)
    client = make_wire_client(server, LLM_TIMEOUT_SECONDS=1, LLM_MAX_RETRIES=2)

    started = time.perf_counter()
    outcomes = client.execute_network_inference(pages(jpegs[:1]))
    elapsed = time.perf_counter() - started

    assert [o.status for o in outcomes] == ["network_failure"]
    assert "network failure" in outcomes[0].error.lower()
    assert len(server.requests) == 2
    assert elapsed < 10
    with pytest.raises(AssertionError, match="Expected a connection refusal"):
        assert_connection_refused(outcomes, caplog.records, 2)


@contextmanager
def reserved_refused_endpoint():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        if sys.platform == "win32":
            reserved.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        reserved.bind(("127.0.0.1", 0))
        yield reserved


def assert_endpoint_reserved(address):
    for reuse in (False, True):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as competitor:
            if reuse:
                competitor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with pytest.raises(OSError) as blocked:
                competitor.bind(address)
            assert blocked.value.errno in (errno.EADDRINUSE, errno.EACCES)


def assert_connection_refused(outcomes, records, attempts):
    assert [o.status for o in outcomes] == ["network_failure"]
    assert "network failure" in outcomes[0].error.lower()
    refusal = "[WinError 10061]" if sys.platform == "win32" else f"[Errno {errno.ECONNREFUSED}]"
    assert refusal in outcomes[0].error, "Expected a connection refusal, not an HTTP response or timeout"
    failed = [r.getMessage() for r in records
              if r.name == "LLMNetworkClient" and r.getMessage().startswith("Network attempt ")]
    assert len(failed) == attempts, "Every configured connection attempt must be observed"
    for number, message in enumerate(failed, start=1):
        assert message.startswith(f"Network attempt {number} failed for request 1:")
        assert refusal in message


@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_provider_not_running_is_a_clean_network_failure(jpegs, caplog, attempts):
    with reserved_refused_endpoint() as reserved:
        address = reserved.getsockname()
        assert_endpoint_reserved(address)
        endpoint = SimpleNamespace(url=f"http://127.0.0.1:{address[1]}/v1/chat/completions")
        client = make_wire_client(endpoint, LLM_MAX_RETRIES=attempts, LLM_TIMEOUT_SECONDS=3)
        with caplog.at_level(logging.WARNING, logger="LLMNetworkClient"):
            outcomes = client.execute_network_inference(pages(jpegs[:1]))
        assert [o.status for o in outcomes] == ["network_failure"]
        assert "network failure" in outcomes[0].error.lower()
        assert_connection_refused(outcomes, caplog.records, attempts)
        assert_endpoint_reserved(address)
    assert reserved.fileno() == -1


@pytest.mark.parametrize("fail_inside", [False, True])
def test_refused_endpoint_closes_even_after_assertion_failure(fail_inside):
    reserved = None
    try:
        with reserved_refused_endpoint() as reserved:
            assert reserved.fileno() != -1
            if fail_inside:
                raise AssertionError("deliberate assertion failure")
    except AssertionError as exc:
        assert fail_inside and str(exc) == "deliberate assertion failure"
    else:
        assert not fail_inside
    assert reserved is not None
    assert reserved.fileno() == -1


@contextmanager
def owned_http_refusal(reserved, status):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.connection.settimeout(3)
            self.rfile.read(int(self.headers["Content-Length"]))
            received.append(self.path)
            body = b'{"error": "controlled refusal"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    address = reserved.getsockname()
    with HTTPServer(address, Handler, bind_and_activate=False) as responder:
        responder.socket.close()
        responder.socket = reserved.dup()
        reserved.close()
        responder.server_activate()
        thread = threading.Thread(target=responder.serve_forever, daemon=True)
        thread.start()
        try:
            yield SimpleNamespace(
                url=f"http://127.0.0.1:{address[1]}/v1/chat/completions", received=received)
        finally:
            responder.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive(), "Owned HTTP responder failed to stop"


@pytest.mark.parametrize("status", [400, 401, 403])
def test_released_reservation_with_owned_http_responder_is_not_connection_refused(jpegs, caplog, status):
    with reserved_refused_endpoint() as reserved, owned_http_refusal(reserved, status) as responder:
        assert reserved.fileno() == -1
        client = make_wire_client(responder, LLM_MAX_RETRIES=2, LLM_TIMEOUT_SECONDS=3)
        if status in (401, 403):
            with pytest.raises(ConfigurationError, match=f"AUTHENTICATION.*HTTP {status}"):
                client.execute_network_inference(pages(jpegs[:1]))
        else:
            outcomes = client.execute_network_inference(pages(jpegs[:1]))
            assert [o.status for o in outcomes] == ["network_failure"]
            assert "network failure" in outcomes[0].error.lower()
            with pytest.raises(AssertionError, match="Expected a connection refusal"):
                assert_connection_refused(outcomes, caplog.records, 2)
        assert responder.received == ["/v1/chat/completions"]


def test_connection_refusal_check_rejects_missing_retry(jpegs, caplog):
    with reserved_refused_endpoint() as reserved:
        endpoint = SimpleNamespace(url=f"http://127.0.0.1:{reserved.getsockname()[1]}/v1/chat/completions")
        client = make_wire_client(endpoint, LLM_MAX_RETRIES=2, LLM_TIMEOUT_SECONDS=3)
        with caplog.at_level(logging.WARNING, logger="LLMNetworkClient"):
            outcomes = client.execute_network_inference(pages(jpegs[:1]))
        assert_connection_refused(outcomes, caplog.records, 2)
        missing_retry = [r for r in caplog.records if not r.getMessage().startswith("Network attempt 2 ")]
        with pytest.raises(AssertionError, match="Every configured connection attempt"):
            assert_connection_refused(outcomes, missing_retry, 2)


def test_rate_limit_429_is_retried_then_recovers(server, jpegs):
    server.queue(status=429, json={"error": {"message": "rate limited"}})
    server.queue(json=openai_reply('{"answer": "after the rate limit"}'))
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["ok"]
    assert "after the rate limit" in outcomes[0].raw_answer
    assert len(server.requests) == 2


def test_a_413_refusal_is_recorded_once_with_the_server_body(server, jpegs):
    server.queue(status=413, json={"type": "error", "error": {
        "type": "request_too_large",
        "message": "Request exceeds the maximum size"}})
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert len(server.requests) == 1
    assert [o.status for o in outcomes] == ["network_failure"]
    assert "HTTP 413" in outcomes[0].error
    assert "request_too_large" in outcomes[0].error


def test_a_400_rejection_is_recorded_once_with_the_server_body(server, jpegs):
    server.queue(status=400, json={"error": {
        "message": "This model does not support image",
        "type": "invalid_request_error"}})
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert len(server.requests) == 1
    assert [o.status for o in outcomes] == ["network_failure"]
    assert "This model does not support image" in outcomes[0].error


@pytest.mark.parametrize("status_code", [408, 409])
def test_timeout_and_conflict_statuses_are_retried(server, jpegs, status_code):
    server.queue(status=status_code, json={"error": {"message": "try again"}})
    server.queue(json=openai_reply('{"answer": "after the retry"}'))
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["ok"]
    assert len(server.requests) == 2


def test_multi_megabyte_answer_with_unicode_survives_intact(server, jpegs):
    huge = "🐸 длинный ответ λ " * 120_000
    server.queue(json=openai_reply(
        json.dumps({"answer": huge}, ensure_ascii=False)))
    client = make_wire_client(server)

    outcomes = client.execute_network_inference(pages(jpegs[:1]))

    assert [o.status for o in outcomes] == ["ok"]
    assert huge in outcomes[0].raw_answer



def test_unreadable_prompt_file_degrades_to_empty_prompt(server, jpegs, caplog):
    with caplog.at_level(logging.ERROR):
        client = make_wire_client(
            server,
            LLM_USER_PROMPT=r"C:\nowhere\missing_prompt.txt",
            LLM_USER_PROMPT_MODE="FILE",
        )

    assert client.user_prompt_string == ""
    assert "Failed to read prompt file" in caplog.text
    outcomes = client.execute_network_inference(pages(jpegs[:1]))
    assert [o.status for o in outcomes] == ["ok"]


def test_one_unreadable_image_is_skipped_but_request_still_sent(server, jpegs, tmp_path):
    missing = tmp_path / "deleted_meanwhile.jpg"

    client = make_wire_client(server)
    outcomes = client.execute_network_inference(pages([jpegs[0], missing, jpegs[1]]))

    assert len(server.requests) == 1
    content = server.requests[0].json["messages"][-1]["content"]
    assert len([c for c in content if c["type"] == "image_url"]) == 2
    texts = [c["text"] for c in content if c["type"] == "text"]
    assert "Image 1 of 3 - page 1." in texts
    assert "Image 3 of 3 - page 3." in texts
    assert not any("Image 2 of 3" in t for t in texts)
    assert [o.status for o in outcomes] == ["ok"]
    assert "Base64 Encoding Failure" in outcomes[0].error


def test_all_images_unreadable_sends_nothing_over_the_wire(server, tmp_path):
    missing = [tmp_path / "a.jpg", tmp_path / "b.jpg"]

    client = make_wire_client(server)
    outcomes = client.execute_network_inference(pages(missing))

    assert len(server.requests) == 0
    assert [o.status for o in outcomes] == ["encoding_failure"]
    assert "Zero valid images encoded" in outcomes[0].error



ALL_SHIPPED_PROVIDERS = sorted(_default_provider_configs().keys())


@pytest.mark.parametrize("provider_name", ALL_SHIPPED_PROVIDERS)
def test_every_shipped_provider_preset_round_trips(server, jpegs, provider_name):
    config = _default_provider_configs()[provider_name].model_copy(
        update={"url": server.url}
    )

    marker = f"round-trip answer for {provider_name}"
    reply_text = json.dumps({"answer": marker})
    if config.reasoning_handling == "parse_claude_blocks":
        server.default_behavior = {"json": claude_reply(reply_text)}
    elif config.reasoning_handling == "strip_xml":
        server.default_behavior = {
            "json": openai_reply(f"<think>internal musing</think>{reply_text}")
        }
    else:
        server.default_behavior = {"json": openai_reply(reply_text)}

    settings = SimpleNamespace(
        ACTIVE_PROVIDER_CONFIG=config,
        MAX_JPEGS_PER_INFERENCE=10,
        LLM_MAX_RETRIES=1,
        LLM_TIMEOUT_SECONDS=15,
        LLM_RETRY_SLEEP_SECONDS=0,
        HALT_ON_LLM_PARSE_ERROR=True,
        LLM_SYSTEM_PROMPT="system prompt",
        LLM_SYSTEM_PROMPT_MODE="TEXT",
        LLM_USER_PROMPT="user prompt",
        LLM_USER_PROMPT_MODE="TEXT",
        LLM_PROVIDER=provider_name,
        LLM_OUTPUT_MODE="table_per_file",
        LLM_OUTPUT_COLUMNS="answer",
        LLM_OUTPUT_COLUMN_INSTRUCTIONS="",
        LLM_JSON_MAX_ATTEMPTS=1,
        LLM_ABORT_ON_MALFORMED_JSON=False,
    )
    client = LLMClient(settings, token=FAKE_TOKEN)

    outcomes = client.execute_network_inference(pages(jpegs[:2]))

    assert [o.status for o in outcomes] == ["ok"]
    assert marker in outcomes[0].raw_answer
    if config.reasoning_handling == "strip_xml":
        assert "internal musing" not in outcomes[0].raw_answer

    recorded = server.requests[-1]
    payload = recorded.json
    headers = {k.lower(): v for k, v in recorded.headers.items()}

    content = payload["messages"][-1]["content"]
    image_kinds = {c["type"] for c in content if c["type"] != "text"}
    if config.image_payload_style == "base64_dict":
        assert image_kinds == {"image"}
    else:
        assert image_kinds == {"image_url"}

    if config.system_prompt_location == "top_level":
        assert payload["system"] == "system prompt"
    else:
        assert payload["messages"][0] == {
            "role": "system", "content": "system prompt"
        }

    ceiling_names = {"max_tokens", "max_completion_tokens"}
    if config.require_max_tokens:
        assert payload[config.max_tokens_field] == config.max_tokens
        assert not (ceiling_names - {config.max_tokens_field}) & set(payload)
    else:
        assert not ceiling_names & set(payload)

    if config.auth_header_key:
        assert config.auth_header_format is not None
        expected = config.auth_header_format.replace("{token}", FAKE_TOKEN)
        assert headers.get(config.auth_header_key.lower()) == expected
    else:
        assert "authorization" not in headers
    if config.extra_header_key:
        assert headers.get(config.extra_header_key.lower()) == config.extra_header_value
