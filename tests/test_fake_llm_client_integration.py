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

from schemas import ConfigurationError, Status


@pytest.fixture
def jpeg(tmp_path):
    p = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8), (10, 20, 30)).save(p, "JPEG")
    return p


@pytest.mark.parametrize("provider", available())
def test_real_client_round_trips_against_each_provider(wire_provider, jpeg, provider):
    srv, client = wire_provider(provider)

    result = client.execute_network_inference([jpeg])

    assert result.status == Status.OK.value, f"{provider}: {result.error}"
    assert "quick brown fox" in result.answer.lower()
    assert srv.requests, f"{provider}: no request reached the server"


@pytest.mark.parametrize("provider", ["openai", "claude", "deepseek"])
def test_wrong_shaped_token_is_rejected_like_the_real_server(
    wire_provider, jpeg, provider
):
    srv, client = wire_provider(provider, token="not-a-real-key")

    with pytest.raises(ConfigurationError, match="FATAL AUTHENTICATION ERROR"):
        client.execute_network_inference([jpeg])
    assert srv.requests, f"{provider}: the rejection never came from the server"



def test_gemini_wrong_shaped_key_must_halt_as_a_credentials_error(
    wire_provider, jpeg
):
    srv, client = wire_provider("gemini", token="not-a-real-key")

    with pytest.raises(ConfigurationError, match="AUTHENTICATION"):
        client.execute_network_inference([jpeg])
    assert srv.requests, "gemini: the rejection never came from the server"
