# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_client import LLMClient
from schemas import Status, ConfigurationError



class CannedResponse:

    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code
        self.text = str(json_body)

    def json(self):
        return self._json_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}", response=self
            )


def make_client(provider_overrides=None, **settings_overrides):
    provider = SimpleNamespace(
        url="https://api.example.test/v1/chat/completions",
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
        LLM_TIMEOUT_SECONDS=5,
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

    return LLMClient(settings, token="sk-fake0123456789abcdef0123456789abcdef")


def stub_http(client, script):
    sent = []

    def fake_post(headers, payload, abort_flag):
        sent.append((headers, payload))
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client._post_with_abort = fake_post
    return sent


def make_images(tmp_path, count=1):
    paths = []
    for i in range(count):
        p = tmp_path / f"frame_{i}.jpg"
        p.write_bytes(b"not-a-real-jpeg-but-bytes-suffice")
        paths.append(p)
    return paths


def openai_reply(text, finish_reason="stop"):
    return CannedResponse(
        {"choices": [{"message": {"content": text}, "finish_reason": finish_reason}]}
    )



def test_parse_claude_blocks_joins_text_blocks(tmp_path):
    client = make_client({"reasoning_handling": "parse_claude_blocks"})
    stub_http(client, [CannedResponse({
        "stop_reason": "end_turn",
        "content": [
            {"type": "text", "text": "First block"},
            {"type": "thinking", "thinking": "internal reasoning, must be dropped"},
            {"type": "text", "text": "Second block"},
        ],
    })])

    result = client.execute_network_inference(make_images(tmp_path))

    assert result.status == Status.OK.value
    assert result.answer == "--- Chunk 1 ---\nFirst block\nSecond block\n"
    assert result.error == ""


def test_dynamic_extraction_path_walks_keys_and_indices(tmp_path):
    client = make_client(
        {"response_extraction_path": "choices[0].message.content"}
    )
    stub_http(client, [openai_reply("Extracted text")])

    result = client.execute_network_inference(make_images(tmp_path))

    assert result.status == Status.OK.value
    assert result.answer == "--- Chunk 1 ---\nExtracted text\n"


def test_strip_xml_removes_matched_think_tags(tmp_path):
    client = make_client({"reasoning_handling": "strip_xml"})
    stub_http(client, [openai_reply("<think>internal chatter</think>The answer")])

    result = client.execute_network_inference(make_images(tmp_path))

    assert result.status == Status.OK.value
    assert result.answer == "--- Chunk 1 ---\nThe answer\n"


def test_strip_xml_mismatched_tags_warn_but_keep_text(tmp_path):
    client = make_client({"reasoning_handling": "strip_xml"})
    stub_http(client, [openai_reply("<think>never closed... The answer")])

    result = client.execute_network_inference(make_images(tmp_path))

    assert result.answer == "--- Chunk 1 ---\n<think>never closed... The answer\n"
    assert "Mismatched <think> tags" in result.error
    assert result.status == Status.LLM_PARTIAL.value



@pytest.mark.parametrize("reply", [
    CannedResponse({"choices": [{"message": {"content": "cut off"},
                                 "finish_reason": "length"}]}),
    CannedResponse({"stop_reason": "max_tokens",
                    "content": [{"type": "text", "text": "cut off"}]}),
])
def test_truncation_halts_batch_when_halt_flag_on(tmp_path, reply):
    provider = (
        {"reasoning_handling": "parse_claude_blocks"}
        if "stop_reason" in reply.json() else None
    )
    client = make_client(provider, HALT_ON_LLM_PARSE_ERROR=True)
    stub_http(client, [reply])

    with pytest.raises(ConfigurationError, match="Token Limit Exceeded"):
        client.execute_network_inference(make_images(tmp_path))


def test_truncation_marks_chunk_and_continues_when_halt_flag_off(tmp_path):
    client = make_client(HALT_ON_LLM_PARSE_ERROR=False, MAX_JPEGS_PER_INFERENCE=1)
    stub_http(client, [
        openai_reply("truncated", finish_reason="length"),
        openai_reply("second chunk fine"),
    ])

    result = client.execute_network_inference(make_images(tmp_path, count=2))

    assert result.answer == (
        "--- Chunk 1 ---\n[TOKEN LIMIT EXCEEDED - SEE LOGS]\n"
        "\n--- Chunk 2 ---\nsecond chunk fine\n"
    )
    assert "Token Limit Exceeded" in result.error
    assert result.status == Status.LLM_PARTIAL.value



@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_rejection_is_fatal(tmp_path, status_code):
    client = make_client()
    stub_http(client, [CannedResponse({"error": "denied"}, status_code=status_code)])

    with pytest.raises(ConfigurationError, match="FATAL AUTHENTICATION ERROR"):
        client.execute_network_inference(make_images(tmp_path))



def test_retry_exhaustion_yields_network_failure_chunk(tmp_path):
    client = make_client(LLM_MAX_RETRIES=3)
    script = [requests.exceptions.ConnectionError("refused") for _ in range(3)]
    sent = stub_http(client, script)

    result = client.execute_network_inference(make_images(tmp_path))

    assert len(sent) == 3, "every configured retry must be attempted"
    assert result.status == Status.LLM_FAILED.value
    assert result.answer == "[TOTAL LLM NETWORK FAILURE]"
    assert "Chunk 1 Network Failure" in result.error


def test_partial_status_when_one_chunk_fails_and_one_succeeds(tmp_path):
    client = make_client(MAX_JPEGS_PER_INFERENCE=1, LLM_MAX_RETRIES=1)
    stub_http(client, [
        openai_reply("chunk one ok"),
        requests.exceptions.ConnectionError("refused"),
    ])

    result = client.execute_network_inference(make_images(tmp_path, count=2))

    assert result.status == Status.LLM_PARTIAL.value
    assert result.answer == (
        "--- Chunk 1 ---\nchunk one ok\n"
        "\n--- Chunk 2 ---\n[NETWORK FAILURE]\n"
    )



def test_empty_answer_is_a_parse_error_marking_the_chunk(tmp_path):
    client = make_client(HALT_ON_LLM_PARSE_ERROR=False)
    stub_http(client, [openai_reply("   \n  ")])

    result = client.execute_network_inference(make_images(tmp_path))

    assert result.status == Status.LLM_FAILED.value
    assert result.answer == "[TOTAL LLM NETWORK FAILURE]"
    assert "Parse Error" in result.error


def test_empty_answer_halts_batch_when_halt_flag_on(tmp_path):
    client = make_client(HALT_ON_LLM_PARSE_ERROR=True)
    stub_http(client, [openai_reply("")])

    with pytest.raises(ConfigurationError, match="FATAL PARSE ERROR"):
        client.execute_network_inference(make_images(tmp_path))


def test_unexpected_schema_is_a_parse_error(tmp_path):
    client = make_client(HALT_ON_LLM_PARSE_ERROR=False, MAX_JPEGS_PER_INFERENCE=1)
    stub_http(client, [
        CannedResponse({"unexpected": "shape"}),
        openai_reply("second chunk fine"),
    ])

    result = client.execute_network_inference(make_images(tmp_path, count=2))

    assert result.status == Status.LLM_PARTIAL.value
    assert result.answer == (
        "--- Chunk 1 ---\n[PARSE ERROR - SEE LOGS]\n"
        "\n--- Chunk 2 ---\nsecond chunk fine\n"
    )
