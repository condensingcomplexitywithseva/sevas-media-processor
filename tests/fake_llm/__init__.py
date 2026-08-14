# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from .core import ProviderServer, MultiServer, RecordedRequest
from .content import ContentPolicy, DEFAULT_ANSWER, DEFAULT_THINKING
from .generic import GenericServer, openai_reply, claude_reply
from .providers import available, get_provider_class, DEFAULT_PORTS


def make_server(name: str, **kwargs) -> ProviderServer:
    return get_provider_class(name)(**kwargs)


def make_all(**kwargs) -> MultiServer:
    return MultiServer([get_provider_class(n)(**kwargs) for n in available()])


__all__ = [
    "ProviderServer", "MultiServer", "RecordedRequest",
    "ContentPolicy", "DEFAULT_ANSWER", "DEFAULT_THINKING",
    "GenericServer", "openai_reply", "claude_reply",
    "make_server", "make_all", "available", "get_provider_class",
    "DEFAULT_PORTS",
]
