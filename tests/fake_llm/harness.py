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

from config_validator import ProviderConfig, _default_provider_configs, token_env_key
from llm_client import LLMClient

from . import make_server

REQUEST_FORMATS: dict[str, dict] = {
    "openai": {
        "path": "/v1/chat/completions",
        "model": "gpt-5.6",
        "max_tokens_field": "max_completion_tokens",
        "reasoning_handling": "preserve",
    },
    "claude": {
        "path": "/v1/messages",
        "model": "claude-sonnet-5",
        "auth_header_key": "x-api-key",
        "auth_header_format": "{token}",
        "extra_header_key": "anthropic-version",
        "extra_header_value": "2023-06-01",
        "require_max_tokens": True,
        "system_prompt_location": "top_level",
        "image_payload_style": "base64_dict",
        "response_extraction_path": "content[0].text",
        "reasoning_handling": "parse_claude_blocks",
    },
    "gemini": {
        "path": "/v1beta/openai/chat/completions",
        "model": "gemini-3.8-flash",
        "reasoning_handling": "preserve",
    },
    "deepseek": {
        "path": "/chat/completions",
        "model": "deepseek-v4-flash-vision-exp",
        "reasoning_handling": "ignore_api_field",
    },
    "mistral": {
        "path": "/v1/chat/completions",
        "model": "mistral-medium-latest",
        "reasoning_handling": "preserve",
    },
    "zai": {
        "path": "/api/paas/v4/chat/completions",
        "model": "GLM-4.6V-Flash",
        "reasoning_handling": "preserve",
    },
    "ollama": {
        "path": "/v1/chat/completions",
        "model": "qwen3.5:0.8b",
        "auth_header_key": "",
        "auth_header_format": "",
        "reasoning_handling": "strip_xml",
    },
    "lm-studio": {
        "path": "/v1/chat/completions",
        "model": "qwen/qwen3.5-2b",
        "auth_header_key": "",
        "auth_header_format": "",
        "reasoning_handling": "strip_xml",
    },
}

TEST_PROVIDER = "custom"

READY_ENTRY = {"url": "http://127.0.0.1:9/v1/chat/completions", "model": "ready-model"}


def ready_ai(**entry_overrides) -> dict:
    return {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": TEST_PROVIDER,
            "LLM_PROVIDERS": {TEST_PROVIDER: {**READY_ENTRY, **entry_overrides}}}


def format_entry(format_name: str, url: str, **overrides) -> dict:
    spec = {key: value for key, value in REQUEST_FORMATS[format_name].items() if key != "path"}
    return {"url": url, **spec, **overrides}


def format_url(format_name: str, base_url: str) -> str:
    return base_url.rstrip("/") + REQUEST_FORMATS[format_name]["path"]


def format_config(format_name: str, base_url: str, **overrides) -> ProviderConfig:
    return ProviderConfig(**format_entry(format_name, format_url(format_name, base_url), **overrides))


def format_settings(format_name: str, base_url: str, **provider_overrides) -> dict:
    return {
        "ENABLE_LLM_INFERENCE": True,
        "LLM_PROVIDER": TEST_PROVIDER,
        "LLM_PROVIDERS": {TEST_PROVIDER: format_entry(
            format_name, format_url(format_name, base_url), **provider_overrides)},
    }


def format_tokens(format_name: str) -> dict[str, str]:
    token = KEY_SHAPED_TOKENS.get(format_name)
    return {token_env_key(TEST_PROVIDER): token} if token else {}

KEY_SHAPED_TOKENS: dict[str, str | None] = {
    "openai": "sk-fake0123456789abcdef0123456789abcdef",
    "claude": "sk-ant-fake0123456789abcdefABCDEF",
    "gemini": "AIzaFAKE0123456789abcdefghijklmnopqrst",
    "deepseek": "sk-fake0123456789abcdef0123456789",
    "mistral": "fake-mistral-token-not-a-real-key",
    "zai": "fake-zai-token-not-a-real-key",
    "ollama": None,
    "lm-studio": None,
}

_DEFAULT_SETTINGS = {
    "MAX_JPEGS_PER_INFERENCE": 10,
    "LLM_MAX_RETRIES": 1,
    "LLM_TIMEOUT_SECONDS": 15,
    "LLM_RETRY_SLEEP_SECONDS": 0,
    "HALT_ON_LLM_PARSE_ERROR": True,
    "LLM_SYSTEM_PROMPT": "You are a helpful assistant. Output only the text.",
    "LLM_SYSTEM_PROMPT_MODE": "TEXT",
    "LLM_USER_PROMPT": "Transcribe all text from these images.",
    "LLM_USER_PROMPT_MODE": "TEXT",
    "LLM_OUTPUT_MODE": "table_per_file",
    "LLM_OUTPUT_COLUMNS": "answer",
    "LLM_OUTPUT_COLUMN_INSTRUCTIONS": "",
    "LLM_JSON_MAX_ATTEMPTS": 3,
    "LLM_ABORT_ON_MALFORMED_JSON": False,
}

class _Auto:
    pass


_AUTO = _Auto()


def _client(provider_name: str, cfg: ProviderConfig, token, token_name: str,
            settings_overrides: dict) -> LLMClient:
    settings_kwargs = dict(_DEFAULT_SETTINGS)
    settings_kwargs.update(settings_overrides)
    settings = SimpleNamespace(
        ACTIVE_PROVIDER_CONFIG=cfg,
        LLM_PROVIDER=provider_name,
        **settings_kwargs,
    )
    if isinstance(token, _Auto):
        token = KEY_SHAPED_TOKENS.get(token_name)
    return LLMClient(settings, token=token)


def build_client(format_name: str, base_url: str, *,
                 token: str | _Auto | None = _AUTO,
                 provider_overrides: dict | None = None,
                 **settings_overrides) -> LLMClient:
    cfg = format_config(format_name, base_url, **(provider_overrides or {}))
    return _client(TEST_PROVIDER, cfg, token, format_name, settings_overrides)


def build_preset_client(provider_name: str, base_url: str, *,
                        token: str | _Auto | None = _AUTO,
                        provider_overrides: dict | None = None,
                        **settings_overrides) -> LLMClient:
    preset = _default_provider_configs()[provider_name]
    path = urlparse(preset.url).path or "/"
    cfg = preset.model_copy(update={"url": base_url.rstrip("/") + path,
                                    **(provider_overrides or {})})
    return _client(provider_name, cfg, token, provider_name, settings_overrides)


@pytest.fixture
def wire_provider():
    started = []

    def _make(format_name: str, *, token=_AUTO, provider_overrides=None,
              **settings_overrides):
        srv = make_server(format_name).start()
        started.append(srv)
        client = build_client(format_name, srv.base_url, token=token,
                              provider_overrides=provider_overrides,
                              **settings_overrides)
        return srv, client

    yield _make

    for srv in started:
        srv.stop()
