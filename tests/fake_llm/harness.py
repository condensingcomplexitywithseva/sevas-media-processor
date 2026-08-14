# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config_validator import _default_provider_configs
from llm_client import LLMClient

from . import make_server

KEY_SHAPED_TOKENS: dict[str, str | None] = {
    "openai": "sk-fake0123456789abcdef0123456789abcdef",
    "claude": "sk-ant-fake0123456789abcdefABCDEF",
    "gemini": "AIzaFAKE0123456789abcdefghijklmnopqrst",
    "deepseek": "sk-fake0123456789abcdef0123456789",
    "mistral": "fake-mistral-token-not-a-real-key",
    "ollama": None,
    "lm-studio": None,
}

_DEFAULT_SETTINGS = dict(
    MAX_JPEGS_PER_INFERENCE=10,
    LLM_MAX_RETRIES=1,
    LLM_TIMEOUT_SECONDS=15,
    LLM_RETRY_SLEEP_SECONDS=0,
    HALT_ON_LLM_PARSE_ERROR=True,
    LLM_SYSTEM_PROMPT="You are a helpful assistant. Output only the text.",
    LLM_SYSTEM_PROMPT_MODE="TEXT",
    LLM_USER_PROMPT="Transcribe all text from these images.",
    LLM_USER_PROMPT_MODE="TEXT",
)

_AUTO = object()


def build_client(provider_name: str, base_url: str, *, token=_AUTO,
                 **settings_overrides) -> LLMClient:
    preset = _default_provider_configs()[provider_name]
    path = urlparse(preset.url).path or "/"
    cfg = preset.model_copy(update={"url": base_url.rstrip("/") + path})

    settings_kwargs = dict(_DEFAULT_SETTINGS)
    settings_kwargs.update(settings_overrides)
    settings = SimpleNamespace(
        ACTIVE_PROVIDER_CONFIG=cfg,
        LLM_PROVIDER=provider_name,
        **settings_kwargs,
    )
    if token is _AUTO:
        token = KEY_SHAPED_TOKENS.get(provider_name)
    return LLMClient(settings, token=token)


@pytest.fixture
def wire_provider():
    started = []

    def _make(provider_name: str, *, token=_AUTO, **settings_overrides):
        srv = make_server(provider_name).start()
        started.append(srv)
        client = build_client(provider_name, srv.base_url, token=token,
                              **settings_overrides)
        return srv, client

    yield _make

    for srv in started:
        srv.stop()
