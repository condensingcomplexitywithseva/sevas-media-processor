# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import logging
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_validator import _default_provider_configs
from llm_client import LLMClient
from schemas import ConfigurationError, Status

from fake_llm.generic import (
    GenericServer as FakeLLMServer, claude_reply, openai_reply,
)
from fake_llm.harness import KEY_SHAPED_TOKENS

FAKE_TOKEN = KEY_SHAPED_TOKENS["openai"]


@pytest.fixture
def server():
    srv = FakeLLMServer().start()
    yield srv
    srv.stop()


def make_wire_client(server, provider_overrides=None, token=FAKE_TOKEN,
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
    result = client.execute_network_inference(jpegs[:1], abort_flag=flag_after(0.3))
    elapsed = time.perf_counter() - started

    assert elapsed < 3.0
    assert result.status == Status.LLM_FAILED.value
    assert "aborted by user" in result.error.lower()


def test_abort_mid_call_is_reported_as_abort_not_network_error(server, jpegs):
    server.queue(stall=True)
    client = make_wire_client(server, LLM_TIMEOUT_SECONDS=30)

    result = client.execute_network_inference(jpegs[:1], abort_flag=flag_after(0.3))

    assert "aborted by user" in result.error.lower()
    assert "network failure" not in result.error.lower()



def test_abortable_sleep_interrupts_within_a_tick(server):
    client = make_wire_client(server)

    started = time.perf_counter()
    interrupted = client._abortable_sleep(10, flag_after(0.2))
    elapsed = time.perf_counter() - started

    assert interrupted is True
    assert elapsed < 1.5



def test_abort_during_first_chunk_prevents_all_further_requests(server, jpegs):
    server.queue(
        json=openai_reply("chunk one answer"),
        on_request=lambda rec: abort.set(),
    )
    abort = threading.Event()
    client = make_wire_client(server, MAX_JPEGS_PER_INFERENCE=1)

    result = client.execute_network_inference(jpegs[:3], abort_flag=abort)

    assert len(server.requests) == 1
    assert result.status == Status.LLM_FAILED.value
    assert "aborted by user" in result.error.lower()
    assert "network failure" not in result.error.lower()



def test_images_split_into_chunks_each_carrying_the_prompt(server, jpegs):
    client = make_wire_client(server, MAX_JPEGS_PER_INFERENCE=2)

    result = client.execute_network_inference(jpegs)

    assert result.status == Status.OK.value
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


def test_claude_style_payload_and_response_over_the_wire(server, jpegs):
    server.default_behavior = {"json": claude_reply("a tiger on a pyramid")}
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

    result = client.execute_network_inference(jpegs[:2])

    assert result.status == Status.OK.value
    assert "a tiger on a pyramid" in result.answer

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

    client.execute_network_inference(jpegs[:1])

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
        result = client.execute_network_inference(jpegs[:1])

    assert result.status == Status.OK.value
    assert "Authorization" not in server.requests[0].headers
    assert "No API key detected" not in caplog.text


def test_token_never_appears_in_logs_even_across_retries(server, jpegs, caplog):
    for _ in range(3):
        server.queue(status=500, json={"error": "internal"})

    with caplog.at_level(logging.DEBUG):
        client = make_wire_client(server)
        result = client.execute_network_inference(jpegs[:1])

    assert result.status == Status.LLM_FAILED.value
    assert FAKE_TOKEN not in caplog.text



def test_happy_path_over_real_http(server, jpegs):
    server.queue(json=openai_reply("the cat sat on the mat"))
    client = make_wire_client(server)

    result = client.execute_network_inference(jpegs[:1])

    assert result.status == Status.OK.value
    assert "the cat sat on the mat" in result.answer
    assert result.error == ""


def test_http_401_halts_the_whole_batch(server, jpegs):
    server.queue(status=401, json={"error": "invalid key"})
    client = make_wire_client(server)

    with pytest.raises(ConfigurationError, match="AUTHENTICATION"):
        client.execute_network_inference(jpegs[:1])
    assert len(server.requests) == 1


def test_transient_500_is_retried_then_succeeds(server, jpegs):
    server.queue(status=500, json={"error": "hiccup"})
    server.queue(json=openai_reply("recovered fine"))
    client = make_wire_client(server)

    result = client.execute_network_inference(jpegs[:1])

    assert result.status == Status.OK.value
    assert "recovered fine" in result.answer
    assert len(server.requests) == 2


def test_retry_exhaustion_records_network_failure(server, jpegs):
    for _ in range(3):
        server.queue(status=500, json={"error": "still down"})
    client = make_wire_client(server)

    result = client.execute_network_inference(jpegs[:1])

    assert result.status == Status.LLM_FAILED.value
    assert len(server.requests) == 3
    assert "network failure" in result.error.lower()


def test_garbage_html_body_is_retried_like_a_network_failure(server, jpegs):
    for _ in range(3):
        server.queue(raw="<html>this is not JSON</html>")
    client = make_wire_client(server)

    result = client.execute_network_inference(jpegs[:1])

    assert result.status == Status.LLM_FAILED.value
    assert len(server.requests) == 3


def test_truncated_reply_halts_when_configured(server, jpegs):
    server.queue(json=openai_reply("half an ans", finish_reason="length"))
    client = make_wire_client(server, HALT_ON_LLM_PARSE_ERROR=True)

    with pytest.raises(ConfigurationError, match="Token Limit"):
        client.execute_network_inference(jpegs[:1])


def test_unresponsive_provider_times_out_instead_of_hanging(server, jpegs):
    server.queue(stall=True)
    server.queue(stall=True)
    client = make_wire_client(server, LLM_TIMEOUT_SECONDS=1, LLM_MAX_RETRIES=2)

    started = time.perf_counter()
    result = client.execute_network_inference(jpegs[:1])
    elapsed = time.perf_counter() - started

    assert result.status == Status.LLM_FAILED.value
    assert "network failure" in result.error.lower()
    assert len(server.requests) == 2
    assert elapsed < 10


def test_provider_not_running_is_a_clean_network_failure(jpegs):
    dead = FakeLLMServer().start()
    url = dead.url
    dead.stop()

    client = make_wire_client(SimpleNamespace(url=url), LLM_MAX_RETRIES=2)

    result = client.execute_network_inference(jpegs[:1])

    assert result.status == Status.LLM_FAILED.value
    assert "network failure" in result.error.lower()


def test_rate_limit_429_is_retried_then_recovers(server, jpegs):
    server.queue(status=429, json={"error": {"message": "rate limited"}})
    server.queue(json=openai_reply("after the rate limit"))
    client = make_wire_client(server)

    result = client.execute_network_inference(jpegs[:1])

    assert result.status == Status.OK.value
    assert "after the rate limit" in result.answer
    assert len(server.requests) == 2


def test_multi_megabyte_answer_with_unicode_survives_intact(server, jpegs):
    huge = "🐸 длинный ответ λ " * 120_000
    server.queue(json=openai_reply(huge))
    client = make_wire_client(server)

    result = client.execute_network_inference(jpegs[:1])

    assert result.status == Status.OK.value
    assert huge.strip() in result.answer



def test_unreadable_prompt_file_degrades_to_empty_prompt(server, jpegs, caplog):
    with caplog.at_level(logging.ERROR):
        client = make_wire_client(
            server,
            LLM_USER_PROMPT=r"C:\nowhere\missing_prompt.txt",
            LLM_USER_PROMPT_MODE="FILE",
        )

    assert client.user_prompt_string == ""
    assert "Failed to read prompt file" in caplog.text
    result = client.execute_network_inference(jpegs[:1])
    assert result.status == Status.OK.value


def test_one_unreadable_image_is_skipped_but_chunk_still_sent(server, jpegs, tmp_path):
    missing = tmp_path / "deleted_meanwhile.jpg"

    client = make_wire_client(server)
    result = client.execute_network_inference([jpegs[0], missing, jpegs[1]])

    assert len(server.requests) == 1
    content = server.requests[0].json["messages"][-1]["content"]
    assert len([c for c in content if c["type"] == "image_url"]) == 2
    assert result.status == Status.LLM_PARTIAL.value
    assert "Base64 Encoding Failure" in result.error


def test_all_images_unreadable_sends_nothing_over_the_wire(server, tmp_path):
    missing = [tmp_path / "a.jpg", tmp_path / "b.jpg"]

    client = make_wire_client(server)
    result = client.execute_network_inference(missing)

    assert len(server.requests) == 0
    assert result.status == Status.LLM_FAILED.value
    assert "Zero valid images encoded" in result.error



ALL_SHIPPED_PROVIDERS = sorted(_default_provider_configs().keys())


@pytest.mark.parametrize("provider_name", ALL_SHIPPED_PROVIDERS)
def test_every_shipped_provider_preset_round_trips(server, jpegs, provider_name):
    config = _default_provider_configs()[provider_name].model_copy(
        update={"url": server.url}
    )

    marker = f"round-trip answer for {provider_name}"
    if config.reasoning_handling == "parse_claude_blocks":
        server.default_behavior = {"json": claude_reply(marker)}
    elif config.reasoning_handling == "strip_xml":
        server.default_behavior = {
            "json": openai_reply(f"<think>internal musing</think>{marker}")
        }
    else:
        server.default_behavior = {"json": openai_reply(marker)}

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
    )
    client = LLMClient(settings, token=FAKE_TOKEN)

    result = client.execute_network_inference(jpegs[:2])

    assert result.status == Status.OK.value
    assert marker in result.answer
    if config.reasoning_handling == "strip_xml":
        assert "internal musing" not in result.answer

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

    if config.require_max_tokens:
        assert payload["max_tokens"] == config.max_tokens
    else:
        assert "max_tokens" not in payload

    if config.auth_header_key:
        expected = config.auth_header_format.replace("{token}", FAKE_TOKEN)
        assert headers.get(config.auth_header_key.lower()) == expected
    else:
        assert "authorization" not in headers
    if config.extra_header_key:
        assert headers.get(config.extra_header_key.lower()) == config.extra_header_value
