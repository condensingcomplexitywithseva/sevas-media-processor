# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Type

from ..core import ProviderServer

_LAZY: dict[str, str] = {
    "openai": "openai:OpenAIServer",
    "claude": "anthropic:AnthropicServer",
    "gemini": "gemini:GeminiServer",
    "deepseek": "deepseek:DeepSeekServer",
    "mistral": "mistral:MistralServer",
    "ollama": "ollama:OllamaServer",
    "lm-studio": "lmstudio:LMStudioServer",
}

DEFAULT_PORTS: dict[str, int] = {
    "openai": 8001,
    "claude": 8002,
    "gemini": 8003,
    "deepseek": 8004,
    "mistral": 8005,
    "ollama": 8006,
    "lm-studio": 8007,
}


def get_provider_class(name: str) -> Type[ProviderServer]:
    import importlib

    if name not in _LAZY:
        raise KeyError(f"unknown provider {name!r}; known: {sorted(_LAZY)}")
    mod_name, cls_name = _LAZY[name].split(":")
    module = importlib.import_module(f"{__name__}.{mod_name}")
    return getattr(module, cls_name)


def available() -> list[str]:
    return list(_LAZY)
