# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from flask import Flask

from .core import ProviderServer


def openai_reply(text, finish_reason="stop"):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": text},
             "finish_reason": finish_reason}
        ]
    }


def claude_reply(text, stop_reason="end_turn"):
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
    }


class GenericServer(ProviderServer):

    name = "generic"
    default_port = 0

    def __init__(self, default_behavior=None):
        self.default_behavior = default_behavior or {
            "json": openai_reply("default fake answer")}
        super().__init__()

    def register_routes(self, app: Flask) -> None:
        return None

    def handle_unknown_route(self):
        rec = self.requests[-1] if self.requests else None
        return self._apply(dict(self.default_behavior), rec)

    @property
    def url(self) -> str:
        return f"{self.base_url}/v1/chat/completions"
