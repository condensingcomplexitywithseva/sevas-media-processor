# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from flask import Flask, request

from ..core import ProviderServer
from ..content import ContentPolicy, estimate_tokens
from . import _openai_compat as oai

_TEXT_MODELS = {
    "glm-5.3", "glm-5.2", "glm-5.1", "glm-5", "glm-4.7", "glm-4.7-flash",
    "glm-4.7-flashx", "glm-4.6", "glm-4.5", "glm-4.5-air", "glm-4.5-x",
    "glm-4.5-airx", "glm-4.5-flash", "glm-4-32b-0414-128k",
}
_VISION_MODELS = {
    "glm-5.3-flashx", "glm-5.3-flash", "glm-4.6v", "glm-4.6v-flash",
    "glm-4.6v-flashx", "glm-4.5v", "autoglm-phone-multilingual",
}
_MAX_TOKENS_RANGE = (1, 131072)


def zai_error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


class ZaiServer(ProviderServer):
    name = "zai"
    default_port = 8008

    def __init__(self, answer: str | None = None, thinking: str | None = None):
        self.content = ContentPolicy(answer, thinking)
        super().__init__()

    def register_routes(self, app: Flask) -> None:
        app.add_url_rule("/api/paas/v4/chat/completions", "chat",
                         self.chat_completions, methods=["POST"])

    def _auth_error(self):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not token:
            return self.json_response(zai_error(
                "1001", "Authentication parameter not received in Header, "
                        "unable to authenticate"), status=401)
        return None

    def chat_completions(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        auth = self._auth_error()
        if auth is not None:
            return auth

        if not rec.json_ok or not isinstance(rec.json, dict):
            return self.json_response(zai_error(
                "1210", "Invalid API parameter, please check the documentation."),
                status=400)
        body = rec.json

        model = body.get("model")
        if not isinstance(model, str) or not model:
            return self.json_response(zai_error(
                "1213", "Missing required parameter: model"), status=400)
        if model.lower() not in _TEXT_MODELS | _VISION_MODELS:
            return self.json_response(zai_error("1211", "Unknown model"), status=400)

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self.json_response(zai_error(
                "1213", "Missing required parameter: messages"), status=400)

        if "max_tokens" in body:
            value = body["max_tokens"]
            low, high = _MAX_TOKENS_RANGE
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                return self.json_response(zai_error(
                    "1214", "Parameter `max_tokens` is invalid. "
                            "Please check the documentation."), status=400)

        thinking_field = body.get("thinking")
        thinking = isinstance(thinking_field, dict) and thinking_field.get("type") == "enabled"

        text = self.content.answer(body)
        prompt_text = oai.flatten_prompt_text(messages)
        reasoning_text = self.content.thinking(body) if thinking else ""

        if body.get("stream"):
            events = oai.chat_completion_sse_events(
                model, text, include_usage=True, prompt_text=prompt_text)
            return self.sse_response(events)

        obj = oai.chat_completion_body(
            model, text, prompt_text=prompt_text,
            reasoning_tokens=estimate_tokens(reasoning_text) if thinking else 0,
            message_extra={"reasoning_content": reasoning_text} if thinking else None)
        obj["request_id"] = obj["id"]
        return self.json_response(obj)
