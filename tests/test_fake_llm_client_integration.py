# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import requests  # noqa: F401
from PIL import Image

from fake_llm import available
from fake_llm.harness import wire_provider, KEY_SHAPED_TOKENS  # noqa: F401

from schemas import ConfigurationError


@pytest.fixture
def jpeg(tmp_path):
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(p, "JPEG")
    return p


@pytest.mark.parametrize("provider", available())
def test_real_client_round_trips_against_each_provider(wire_provider, jpeg, provider):
    srv, client = wire_provider(provider)

    outcomes = client.execute_network_inference([(1, jpeg, "")])

    assert [o.status for o in outcomes] == ["ok"], \
        f"{provider}: {[o.error for o in outcomes]}"
    assert "quick brown fox" in outcomes[0].raw_answer.lower()
    assert srv.requests, f"{provider}: no request reached the server"


@pytest.mark.parametrize("provider", ["openai", "claude", "deepseek"])
def test_wrong_shaped_token_is_rejected_like_the_real_server(
    wire_provider, jpeg, provider
):
    srv, client = wire_provider(provider, token="not-a-real-key")

    with pytest.raises(ConfigurationError, match="FATAL AUTHENTICATION ERROR"):
        client.execute_network_inference([(1, jpeg, "")])
    assert srv.requests, f"{provider}: the rejection never came from the server"



def test_openai_preset_with_the_ceiling_ticked_is_accepted(wire_provider, jpeg):
    srv, client = wire_provider("openai",
                                provider_overrides={"require_max_tokens": True})

    outcomes = client.execute_network_inference([(1, jpeg, "")])

    assert [o.status for o in outcomes] == ["ok"], [o.error for o in outcomes]
    sent = srv.requests[-1].json
    assert sent["max_completion_tokens"] == client.maximum_output_tokens
    assert "max_tokens" not in sent


def test_openai_rejects_the_classic_ceiling_name_once(wire_provider, jpeg):
    srv, client = wire_provider(
        "openai", provider_overrides={"require_max_tokens": True,
                                      "max_tokens_field": "max_tokens"})

    outcomes = client.execute_network_inference([(1, jpeg, "")])

    assert [o.status for o in outcomes] == ["network_failure"]
    assert len(srv.requests) == 1
    assert "max_completion_tokens" in outcomes[0].error


def test_deepseek_text_model_refuses_images_once(wire_provider, jpeg):
    srv, client = wire_provider(
        "deepseek", provider_overrides={"model": "deepseek-v4-flash"})

    outcomes = client.execute_network_inference([(1, jpeg, "")])

    assert [o.status for o in outcomes] == ["network_failure"]
    assert len(srv.requests) == 1
    assert "This model does not support image" in outcomes[0].error


def test_gemini_wrong_shaped_key_must_halt_as_a_credentials_error(
    wire_provider, jpeg
):
    srv, client = wire_provider("gemini", token="not-a-real-key")

    with pytest.raises(ConfigurationError, match="AUTHENTICATION"):
        client.execute_network_inference([(1, jpeg, "")])
    assert srv.requests, "gemini: the rejection never came from the server"
