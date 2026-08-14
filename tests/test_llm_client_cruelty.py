# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from PIL import Image

from fake_llm.harness import wire_provider
from schemas import Status


@pytest.fixture
def jpeg(tmp_path):
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(p, "JPEG")
    return p


def usage(completion_tokens=0):
    return {"prompt_tokens": 10, "completion_tokens": completion_tokens,
            "total_tokens": 10 + completion_tokens}



def test_openai_content_filter_refusal_fails_the_chunk_gracefully(wire_provider, jpeg):
    srv, client = wire_provider("openai", HALT_ON_LLM_PARSE_ERROR=False)
    srv.queue(json={
        "id": "chatcmpl-fake", "object": "chat.completion",
        "created": 1780000000, "model": "gpt-5-mini",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "",
                        "refusal": None, "annotations": []},
            "logprobs": None,
            "finish_reason": "content_filter",
        }],
        "usage": usage(),
    })

    result = client.execute_network_inference([jpeg])

    assert result.status == Status.LLM_FAILED.value
    assert result.error, "the refusal must be surfaced, not swallowed"
    assert srv.requests, "the refusal must have come over the wire"


def test_claude_refusal_stop_reason_fails_the_chunk_gracefully(wire_provider, jpeg):
    srv, client = wire_provider("claude", HALT_ON_LLM_PARSE_ERROR=False)
    srv.queue(json={
        "id": "msg_fake", "type": "message", "role": "assistant",
        "model": "claude-sonnet-5", "content": [],
        "stop_reason": "refusal",
        "stop_details": {"type": "refusal", "category": None,
                         "explanation": "Declined to transcribe this image."},
        "stop_sequence": None,
        "usage": {"input_tokens": 50, "output_tokens": 0},
    })

    result = client.execute_network_inference([jpeg])

    assert result.status == Status.LLM_FAILED.value
    assert result.error, "the refusal must be surfaced, not swallowed"



def test_empty_choices_array_fails_the_chunk_gracefully(wire_provider, jpeg):
    srv, client = wire_provider("openai", HALT_ON_LLM_PARSE_ERROR=False)
    srv.queue(json={
        "id": "chatcmpl-fake", "object": "chat.completion",
        "created": 1780000000, "model": "gpt-5-mini",
        "choices": [], "usage": usage(),
    })

    result = client.execute_network_inference([jpeg])

    assert result.status == Status.LLM_FAILED.value
    assert result.error



def test_429_with_retry_after_recovers_on_the_next_attempt(wire_provider, jpeg):
    srv, client = wire_provider("mistral", LLM_MAX_RETRIES=2,
                                LLM_RETRY_SLEEP_SECONDS=0)
    srv.queue(
        status=429,
        json={"object": "error", "message": "Rate limit exceeded",
              "type": "rate_limited", "param": None, "code": "1300"},
        headers={"Retry-After": "2"},
    )

    result = client.execute_network_inference([jpeg])

    assert result.status == Status.OK.value, result.error
    assert "quick brown fox" in result.answer.lower()
    assert len(srv.requests) == 2, "exactly one retry after the 429"


def test_429_storm_exhausts_retries_with_the_real_body_preserved(wire_provider, jpeg):
    srv, client = wire_provider("openai", LLM_MAX_RETRIES=2,
                                LLM_RETRY_SLEEP_SECONDS=0)
    for _ in range(2):
        srv.queue(
            status=429,
            json={"error": {
                "message": "Rate limit reached for gpt-5-mini: 3 requests "
                           "per min. Please try again in 20s.",
                "type": "requests", "param": None,
                "code": "rate_limit_exceeded",
            }},
            headers={"retry-after": "20",
                     "x-ratelimit-remaining-requests": "0"},
        )

    result = client.execute_network_inference([jpeg])

    assert result.status == Status.LLM_FAILED.value
    assert "429" in result.error
    assert "rate_limit_exceeded" in result.error, \
        "the provider's own 429 body must survive into the surfaced error"
    assert len(srv.requests) == 2, "both attempts must have hit the server"



FULL_REPLY = {
    "id": "chatcmpl-fake", "object": "chat.completion",
    "created": 1780000000, "model": "gpt-5-mini",
    "choices": [{
        "index": 0,
        "message": {"role": "assistant",
                    "content": "The quick brown fox jumps over the lazy dog.",
                    "refusal": None, "annotations": []},
        "logprobs": None, "finish_reason": "stop",
    }],
    "usage": usage(12),
}


def test_mid_download_disconnect_is_retried_then_recovers(wire_provider, jpeg):
    srv, client = wire_provider("openai", LLM_MAX_RETRIES=2,
                                LLM_RETRY_SLEEP_SECONDS=0)
    srv.queue(json=FULL_REPLY, truncate_body_after=40)

    result = client.execute_network_inference([jpeg])

    assert result.status == Status.OK.value, result.error
    assert "quick brown fox" in result.answer.lower()
    assert len(srv.requests) == 2, "the torn download must have been retried"


def test_persistent_mid_download_disconnect_exhausts_retries(wire_provider, jpeg):
    srv, client = wire_provider("openai", LLM_MAX_RETRIES=2,
                                LLM_RETRY_SLEEP_SECONDS=0)
    for _ in range(2):
        srv.queue(json=FULL_REPLY, truncate_body_after=40)

    result = client.execute_network_inference([jpeg])

    assert result.status == Status.LLM_FAILED.value
    assert "quick brown fox" not in result.answer.lower(), \
        "a torn body must never be passed off as a real answer"
    assert len(srv.requests) == 2
