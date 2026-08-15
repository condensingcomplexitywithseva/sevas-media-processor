# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json

from flask import Flask, request

from ..core import ProviderServer
from ..content import ContentPolicy, estimate_tokens
from ._openai_compat import rand_suffix

_MODELS = {
    "claude-3-7-sonnet-latest",
    "claude-opus-4-8",
    "claude-sonnet-5",
    "claude-haiku-4-5",
    "claude-fable-5",
    "claude-haiku-4-5-20251001",
}

_DISPLAY_NAMES = {
    "claude-3-7-sonnet-latest": "Claude 3.7 Sonnet",
    "claude-opus-4-8": "Claude Opus 4.8",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
    "claude-fable-5": "Claude Fable 5",
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5 (2025-10-01)",
}

_ADAPTIVE_MODELS = {"claude-fable-5", "claude-opus-4-8", "claude-sonnet-5"}

_VALID_VERSIONS = {"2023-06-01", "2023-01-01"}

_STATUS_FOR_TYPE = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "request_too_large": 413,
    "rate_limit_error": 429,
    "api_error": 500,
    "overloaded_error": 529,
}


class AnthropicServer(ProviderServer):
    name = "claude"
    default_port = 8002

    def __init__(self, answer: str | None = None, thinking: str | None = None):
        self.content = ContentPolicy(answer, thinking)
        super().__init__()


    def register_routes(self, app: Flask) -> None:
        app.add_url_rule("/v1/messages", "messages",
                         self.messages, methods=["POST"])
        app.add_url_rule("/v1/messages/count_tokens", "count_tokens",
                         self.count_tokens, methods=["POST"])
        app.add_url_rule("/v1/models", "models",
                         self.list_models, methods=["GET"])
        app.add_url_rule("/v1/models/<path:model>", "model",
                         self.get_model, methods=["GET"])

    def _decorate(self, resp) -> None:
        resp.headers["request-id"] = "req_" + rand_suffix(24)


    def _error(self, type: str, message: str):
        status = _STATUS_FOR_TYPE.get(type, 400)
        return self.json_response({
            "type": "error",
            "error": {"type": type, "message": message},
            "request_id": "req_" + rand_suffix(24),
        }, status=status)


    def _auth_error(self):
        api_key = request.headers.get("x-api-key")
        authorization = request.headers.get("Authorization")

        if api_key and authorization:
            return self._error(
                "authentication_error",
                "Expected either x-api-key or Authorization header, but "
                "received both. Please use only one authentication method.",
            )
        if not api_key:
            return self._error(
                "authentication_error",
                "x-api-key header is required",
            )
        if not api_key.startswith("sk-ant-"):
            return self._error(
                "authentication_error",
                "invalid x-api-key",
            )
        return None

    def _version_error(self):
        version = request.headers.get("anthropic-version")
        if not version:
            return self._error(
                "invalid_request_error",
                "anthropic-version header is required",
            )
        if version not in _VALID_VERSIONS:
            return self._error(
                "invalid_request_error",
                f"anthropic-version: {version} is not a valid version. Please "
                "refer to https://docs.anthropic.com/en/api/versioning for a "
                "list of valid versions.",
            )
        return None


    def messages(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        auth = self._auth_error()
        if auth is not None:
            return auth

        version = self._version_error()
        if version is not None:
            return version

        if not rec.json_ok or not isinstance(rec.json, dict):
            return self._error(
                "invalid_request_error",
                "Could not parse the JSON body of your request.",
            )
        body = rec.json

        if "model" not in body or not isinstance(body["model"], str):
            return self._error(
                "invalid_request_error",
                "model: Field required",
            )
        model = body["model"]
        if model not in _MODELS:
            return self._error(
                "not_found_error",
                f"model: {model}",
            )

        if "max_tokens" not in body:
            return self._error(
                "invalid_request_error",
                "max_tokens: Field required",
            )
        max_tokens = body["max_tokens"]
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) \
                or max_tokens < 1:
            return self._error(
                "invalid_request_error",
                "max_tokens: Input should be a valid integer greater than or "
                "equal to 1",
            )

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._error(
                "invalid_request_error",
                "messages: Field required",
            )
        first = messages[0]
        if not isinstance(first, dict) or first.get("role") != "user":
            return self._error(
                "invalid_request_error",
                "messages: first message must use the \"user\" role",
            )
        for i, m in enumerate(messages):
            role = m.get("role") if isinstance(m, dict) else None
            if role not in ("user", "assistant"):
                return self._error(
                    "invalid_request_error",
                    f"messages.{i}.role: Input should be 'user' or 'assistant'",
                )

        if model in _ADAPTIVE_MODELS:
            for p in ("temperature", "top_p", "top_k"):
                if p in body:
                    return self._error(
                        "invalid_request_error",
                        f"{p}: `{p}` is not supported with {model}.",
                    )
            last = messages[-1]
            if isinstance(last, dict) and last.get("role") == "assistant":
                return self._error(
                    "invalid_request_error",
                    "messages: prefilling the assistant response (a trailing "
                    f"assistant message) is not supported with {model}.",
                )

        thinking = body.get("thinking")
        if isinstance(thinking, dict):
            if model in _ADAPTIVE_MODELS:
                if "budget_tokens" in thinking:
                    return self._error(
                        "invalid_request_error",
                        "thinking.budget_tokens: Extra inputs are not "
                        f"permitted. `budget_tokens` was removed for {model}; "
                        "use thinking: {\"type\": \"adaptive\"}.",
                    )
            elif thinking.get("type") == "adaptive":
                return self._error(
                    "invalid_request_error",
                    f"thinking.type: adaptive thinking is not supported by "
                    f"{model}.",
                )

        answer = self.content.answer(body)
        wants_thinking = (isinstance(thinking, dict)
                          and thinking.get("type") in ("adaptive", "enabled"))

        content_blocks = []
        thinking_tokens = 0
        if wants_thinking:
            thinking_text = self.content.thinking(body)
            thinking_tokens = estimate_tokens(thinking_text)
            content_blocks.append({"type": "thinking", "thinking": thinking_text})
        content_blocks.append({"type": "text", "text": answer})

        input_tokens = estimate_tokens(self._prompt_text(messages, body)) or 10
        output_tokens = estimate_tokens(answer) + thinking_tokens

        if body.get("stream"):
            events = self._stream_events(
                model, answer,
                thinking_text=(self.content.thinking(body) if wants_thinking
                               else None),
                input_tokens=input_tokens, output_tokens=output_tokens)
            return self.sse_response(events)

        obj = {
            "id": "msg_" + rand_suffix(24),
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content_blocks,
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "stop_details": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
        return self.json_response(obj)


    def _stream_events(self, model, answer, *, thinking_text,
                       input_tokens, output_tokens):
        def ev(event_type, data):
            return (f"event: {event_type}\n"
                    f"data: {json.dumps(data, ensure_ascii=False)}")

        events = []

        events.append(ev("message_start", {
            "type": "message_start",
            "message": {
                "id": "msg_" + rand_suffix(24),
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "stop_details": None,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        }))

        index = 0

        if thinking_text is not None:
            events.append(ev("content_block_start", {
                "type": "content_block_start", "index": index,
                "content_block": {"type": "thinking", "thinking": ""},
            }))
            for piece in self._chunks(thinking_text):
                events.append(ev("content_block_delta", {
                    "type": "content_block_delta", "index": index,
                    "delta": {"type": "thinking_delta", "thinking": piece},
                }))
            events.append(ev("content_block_stop", {
                "type": "content_block_stop", "index": index,
            }))
            index += 1

        events.append(ev("content_block_start", {
            "type": "content_block_start", "index": index,
            "content_block": {"type": "text", "text": ""},
        }))
        for piece in self._chunks(answer):
            events.append(ev("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "text_delta", "text": piece},
            }))
        events.append(ev("content_block_stop", {
            "type": "content_block_stop", "index": index,
        }))

        events.append(ev("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None,
                      "stop_details": None},
            "usage": {"output_tokens": output_tokens},
        }))
        events.append(ev("message_stop", {"type": "message_stop"}))
        return events

    @staticmethod
    def _chunks(text: str, n: int = 4) -> list[str]:
        words = text.split(" ")
        if len(words) <= n:
            return [words[0]] + [" " + w for w in words[1:]] if words else [""]
        size = max(1, len(words) // n)
        out, i = [], 0
        while i < len(words):
            out.append(" ".join(words[i:i + size]))
            i += size
        return [out[0]] + [" " + p for p in out[1:]]


    def count_tokens(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        auth = self._auth_error()
        if auth is not None:
            return auth
        version = self._version_error()
        if version is not None:
            return version

        if not rec.json_ok or not isinstance(rec.json, dict):
            return self._error(
                "invalid_request_error",
                "Could not parse the JSON body of your request.",
            )
        body = rec.json

        if "model" not in body or not isinstance(body["model"], str):
            return self._error("invalid_request_error", "model: Field required")
        if body["model"] not in _MODELS:
            return self._error("not_found_error", f"model: {body['model']}")

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._error("invalid_request_error", "messages: Field required")

        input_tokens = estimate_tokens(self._prompt_text(messages, body)) or 10
        return self.json_response({"input_tokens": input_tokens})


    def list_models(self):
        _rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        version = self._version_error()
        if version is not None:
            return version

        data = [{
            "type": "model",
            "id": m,
            "display_name": _DISPLAY_NAMES.get(m, m),
            "created_at": "2026-01-01T00:00:00Z",
        } for m in sorted(_MODELS)]
        return self.json_response({
            "data": data,
            "has_more": False,
            "first_id": data[0]["id"] if data else None,
            "last_id": data[-1]["id"] if data else None,
        })

    def get_model(self, model: str):
        _rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        version = self._version_error()
        if version is not None:
            return version

        if model not in _MODELS:
            return self._error("not_found_error", f"model: {model}")
        return self.json_response({
            "type": "model",
            "id": model,
            "display_name": _DISPLAY_NAMES.get(model, model),
            "created_at": "2026-01-01T00:00:00Z",
        })


    @staticmethod
    def _prompt_text(messages, body) -> str:
        out: list[str] = []
        system = body.get("system")
        if isinstance(system, str):
            out.append(system)
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    out.append(block["text"])
        for m in messages:
            if not isinstance(m, dict):
                continue
            c = m.get("content")
            if isinstance(c, str):
                out.append(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        out.append(part["text"])
        return " ".join(out)
