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
from schemas import ConfigurationError



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
        max_tokens_field="max_tokens",
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
        LLM_OUTPUT_MODE="table_per_file",
        LLM_OUTPUT_COLUMNS="answer",
        LLM_OUTPUT_COLUMN_INSTRUCTIONS="",
        LLM_JSON_MAX_ATTEMPTS=1,
        LLM_ABORT_ON_MALFORMED_JSON=False,
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
    frames = []
    for i in range(count):
        p = tmp_path / f"frame_{i}.jpg"
        p.write_bytes(b"not-a-real-jpeg-but-bytes-suffice")
        frames.append((i + 1, p, ""))
    return frames


def openai_reply(text, finish_reason="stop"):
    return CannedResponse(
        {"choices": [{"message": {"content": text}, "finish_reason": finish_reason}]}
    )



def test_parse_claude_blocks_joins_text_blocks(tmp_path):
    client = make_client({"reasoning_handling": "parse_claude_blocks"})
    stub_http(client, [CannedResponse({
        "stop_reason": "end_turn",
        "content": [
            {"type": "text", "text": '{"answer":'},
            {"type": "thinking", "thinking": "internal reasoning, must be dropped"},
            {"type": "text", "text": '"both blocks"}'},
        ],
    })])

    outcomes = client.execute_network_inference(make_images(tmp_path))

    assert [o.status for o in outcomes] == ["ok"]
    assert outcomes[0].raw_answer == '{"answer":\n"both blocks"}'
    assert outcomes[0].error == ""


def test_dynamic_extraction_path_walks_keys_and_indices(tmp_path):
    client = make_client(
        {"response_extraction_path": "choices[0].message.content"}
    )
    stub_http(client, [openai_reply('{"answer": "Extracted text"}')])

    outcomes = client.execute_network_inference(make_images(tmp_path))

    assert [o.status for o in outcomes] == ["ok"]
    assert outcomes[0].raw_answer == '{"answer": "Extracted text"}'


def test_strip_xml_removes_matched_think_tags(tmp_path):
    client = make_client({"reasoning_handling": "strip_xml"})
    stub_http(client, [openai_reply(
        '<think>internal chatter</think>{"answer": "The answer"}')])

    outcomes = client.execute_network_inference(make_images(tmp_path))

    assert [o.status for o in outcomes] == ["ok"]
    assert outcomes[0].raw_answer == '{"answer": "The answer"}'


def test_strip_xml_mismatched_tags_warn_but_keep_text(tmp_path):
    client = make_client({"reasoning_handling": "strip_xml"})
    stub_http(client, [openai_reply(
        '<think>never closed... {"answer": "The answer"}')])

    outcomes = client.execute_network_inference(make_images(tmp_path))

    assert [o.status for o in outcomes] == ["ok"]
    assert outcomes[0].raw_answer == '<think>never closed... {"answer": "The answer"}'
    assert "Mismatched <think> tags" in outcomes[0].error



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

    with pytest.raises(ConfigurationError, match="Token Limit Exceeded") as excinfo:
        client.execute_network_inference(make_images(tmp_path))

    outcomes = excinfo.value.request_outcomes or []
    assert [o.status for o in outcomes] == ["token_limit_exceeded"]
    assert outcomes[0].raw_answer == "cut off"


def test_truncation_marks_request_and_continues_when_halt_flag_off(tmp_path):
    client = make_client(HALT_ON_LLM_PARSE_ERROR=False, MAX_JPEGS_PER_INFERENCE=1)
    stub_http(client, [
        openai_reply("truncated", finish_reason="length"),
        openai_reply('{"answer": "second request fine"}'),
    ])

    outcomes = client.execute_network_inference(make_images(tmp_path, count=2))

    assert [o.status for o in outcomes] == ["token_limit_exceeded", "ok"]
    assert outcomes[0].raw_answer == "truncated"
    assert "Token Limit Exceeded" in outcomes[0].error
    assert outcomes[1].raw_answer == '{"answer": "second request fine"}'


def test_truncation_with_nothing_extractable_keeps_the_reply_body(tmp_path):
    client = make_client(HALT_ON_LLM_PARSE_ERROR=False)
    stub_http(client, [CannedResponse(
        {"choices": [{"message": {}, "finish_reason": "length"}]})])

    outcomes = client.execute_network_inference(make_images(tmp_path))

    assert [o.status for o in outcomes] == ["token_limit_exceeded"]
    assert "finish_reason" in outcomes[0].raw_answer



@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_rejection_is_fatal(tmp_path, status_code):
    client = make_client()
    stub_http(client, [CannedResponse({"error": "denied"}, status_code=status_code)])

    with pytest.raises(ConfigurationError, match="FATAL AUTHENTICATION ERROR"):
        client.execute_network_inference(make_images(tmp_path))


def test_auth_halt_carries_every_request_outcome_for_persistence(tmp_path):
    client = make_client(MAX_JPEGS_PER_INFERENCE=1)
    stub_http(client, [
        openai_reply('{"answer": "first answer"}'),
        CannedResponse({"error": "denied"}, status_code=401),
    ])

    with pytest.raises(ConfigurationError, match="FATAL AUTHENTICATION ERROR") as excinfo:
        client.execute_network_inference(make_images(tmp_path, count=3))

    outcomes = excinfo.value.request_outcomes or []
    assert [o.status for o in outcomes] == \
        ["ok", "network_failure", "not_attempted"]
    assert outcomes[0].raw_answer == '{"answer": "first answer"}'
    assert "FATAL AUTHENTICATION ERROR" in outcomes[1].error
    assert "Not attempted" in outcomes[2].error



def test_retry_exhaustion_yields_network_failure_request(tmp_path):
    client = make_client(LLM_MAX_RETRIES=3)
    script = [requests.exceptions.ConnectionError("refused") for _ in range(3)]
    sent = stub_http(client, script)

    outcomes = client.execute_network_inference(make_images(tmp_path))

    assert len(sent) == 3, "every configured retry must be attempted"
    assert [o.status for o in outcomes] == ["network_failure"]
    assert outcomes[0].raw_answer == ""
    assert "Request 1 Network Failure" in outcomes[0].error


@pytest.mark.parametrize("status_code", [400, 404, 413, 422])
def test_a_rejected_request_is_recorded_once_and_never_re_sent(tmp_path, status_code):
    client = make_client(LLM_MAX_RETRIES=3)
    script = [CannedResponse({"error": {"message": "the server's own reason"}},
                             status_code=status_code)]
    sent = stub_http(client, script)

    outcomes = client.execute_network_inference(make_images(tmp_path))

    assert len(sent) == 1, "a rejected request must not be re-sent"
    assert [o.status for o in outcomes] == ["network_failure"]
    assert f"HTTP {status_code}" in outcomes[0].error
    assert "the server's own reason" in outcomes[0].error, \
        "the report carries the server's body verbatim"


@pytest.mark.parametrize("status_code", [408, 409, 429, 500, 503, 529])
def test_a_transient_status_is_retried_then_succeeds(tmp_path, status_code):
    client = make_client(LLM_MAX_RETRIES=3)
    script = [CannedResponse({"error": "later"}, status_code=status_code),
              openai_reply('{"answer": "recovered"}')]
    sent = stub_http(client, script)

    outcomes = client.execute_network_inference(make_images(tmp_path))

    assert len(sent) == 2
    assert [o.status for o in outcomes] == ["ok"]


def test_partial_status_when_one_request_fails_and_one_succeeds(tmp_path):
    client = make_client(MAX_JPEGS_PER_INFERENCE=1, LLM_MAX_RETRIES=1)
    stub_http(client, [
        openai_reply('{"answer": "request one ok"}'),
        requests.exceptions.ConnectionError("refused"),
    ])

    outcomes = client.execute_network_inference(make_images(tmp_path, count=2))

    assert [o.status for o in outcomes] == ["ok", "network_failure"]
    assert outcomes[0].raw_answer == '{"answer": "request one ok"}'
    assert outcomes[1].raw_answer == ""



def test_empty_answer_is_a_parse_error_marking_the_request(tmp_path):
    client = make_client(HALT_ON_LLM_PARSE_ERROR=False)
    stub_http(client, [openai_reply("   \n  ")])

    outcomes = client.execute_network_inference(make_images(tmp_path))

    assert [o.status for o in outcomes] == ["provider_reply_parse_error"]
    assert "choices" in outcomes[0].raw_answer
    assert "Parse Error" in outcomes[0].error


def test_empty_answer_halts_batch_when_halt_flag_on(tmp_path):
    client = make_client(HALT_ON_LLM_PARSE_ERROR=True)
    stub_http(client, [openai_reply("")])

    with pytest.raises(ConfigurationError, match="FATAL PARSE ERROR") as excinfo:
        client.execute_network_inference(make_images(tmp_path))

    outcomes = excinfo.value.request_outcomes or []
    assert [o.status for o in outcomes] == ["provider_reply_parse_error"]
    assert "choices" in outcomes[0].raw_answer


def test_unexpected_schema_is_a_parse_error(tmp_path):
    client = make_client(HALT_ON_LLM_PARSE_ERROR=False, MAX_JPEGS_PER_INFERENCE=1)
    stub_http(client, [
        CannedResponse({"unexpected": "shape"}),
        openai_reply('{"answer": "second request fine"}'),
    ])

    outcomes = client.execute_network_inference(make_images(tmp_path, count=2))

    assert [o.status for o in outcomes] == ["provider_reply_parse_error", "ok"]
    assert "unexpected" in outcomes[0].raw_answer
    assert outcomes[1].raw_answer == '{"answer": "second request fine"}'
