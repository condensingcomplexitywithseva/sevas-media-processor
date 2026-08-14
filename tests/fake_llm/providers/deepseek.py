# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from flask import Flask, request

from ..core import ProviderServer
from ..content import ContentPolicy, estimate_tokens
from . import _openai_compat as oai

_CHAT_MODELS = {
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
}

_SYSTEM_FINGERPRINT = "fp_deepseek_v4"


class DeepSeekServer(ProviderServer):
    name = "deepseek"
    default_port = 8004

    def __init__(self, answer: str | None = None, thinking: str | None = None):
        self.content = ContentPolicy(answer, thinking)
        super().__init__()


    def register_routes(self, app: Flask) -> None:
        app.add_url_rule("/chat/completions", "chat",
                         self.chat_completions, methods=["POST"])
        app.add_url_rule("/v1/chat/completions", "chat_v1",
                         self.chat_completions, methods=["POST"])
        app.add_url_rule("/models", "models", self.list_models, methods=["GET"])
        app.add_url_rule("/v1/models", "models_v1", self.list_models,
                         methods=["GET"])
        app.add_url_rule("/user/balance", "balance", self.user_balance,
                         methods=["GET"])


    def _auth_error(self):
        header = request.headers.get("Authorization")
        token = ""
        if header and header.lower().startswith("bearer "):
            token = header[7:]
        if not token.startswith("sk-"):
            return self.json_response(oai.oai_error(
                "Authentication Fails (no such user)",
                type="authentication_error", code="invalid_request_error",
            ), status=401)
        return None


    @staticmethod
    def _is_thinking(model: str, body: dict) -> bool:
        if model == "deepseek-reasoner":
            return True
        thinking = body.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "enabled":
            return True
        if "reasoning_effort" in body:
            return True
        return False


    def chat_completions(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        auth = self._auth_error()
        if auth is not None:
            return auth

        if not rec.json_ok:
            return self.json_response(oai.oai_error(
                "We could not parse the JSON body of your request.",
                code="invalid_request_error"), status=400)
        body = rec.json

        if not isinstance(body, dict) or "model" not in body:
            return self.json_response(oai.oai_error(
                "Missing required parameter: 'model'.", param="model",
                code="invalid_request_error"), status=400)
        model = body["model"]

        if model not in _CHAT_MODELS:
            return self.json_response(oai.oai_error(
                f"Model Not Exist: {model}", code="model_not_found"),
                status=400)

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self.json_response(oai.oai_error(
                "Missing required parameter: 'messages'.", param="messages",
                code="invalid_request_error"), status=400)

        for m in messages:
            if isinstance(m, dict) and "reasoning_content" in m:
                return self.json_response(oai.oai_error(
                    "reasoning_content is not allowed in the input messages. "
                    "Please remove it before sending the request.",
                    code="invalid_request_error"), status=400)

        thinking = self._is_thinking(model, body)

        text = self.content.answer(body)
        prompt_text = oai.flatten_prompt_text(messages)
        reasoning_text = self.content.thinking(body) if thinking else ""
        reasoning_tokens = estimate_tokens(reasoning_text) if thinking else 0

        if body.get("stream"):
            return self._stream(model, text, prompt_text, thinking,
                                 reasoning_text, reasoning_tokens)

        message_extra = {"reasoning_content": reasoning_text} if thinking else None

        obj = oai.chat_completion_body(
            model, text, prompt_text=prompt_text,
            system_fingerprint=_SYSTEM_FINGERPRINT,
            reasoning_tokens=reasoning_tokens,
            message_extra=message_extra)

        prompt_tokens = obj["usage"]["prompt_tokens"]
        obj["usage"]["prompt_cache_hit_tokens"] = 0
        obj["usage"]["prompt_cache_miss_tokens"] = prompt_tokens
        return self.json_response(obj)


    def _stream(self, model, text, prompt_text, thinking, reasoning_text,
                reasoning_tokens):
        import json as _json

        events = oai.chat_completion_sse_events(
            model, text, system_fingerprint=_SYSTEM_FINGERPRINT,
            include_usage=True, prompt_text=prompt_text,
            reasoning_tokens=reasoning_tokens, done=False)

        cid = None
        cts = None
        first = _json.loads(events[0][len("data: "):])
        cid, cts = first["id"], first["created"]

        def frame(delta):
            obj = {
                "id": cid, "object": "chat.completion.chunk", "created": cts,
                "model": model, "system_fingerprint": _SYSTEM_FINGERPRINT,
                "choices": [{"index": 0, "delta": delta,
                             "logprobs": None, "finish_reason": None}],
            }
            return "data: " + _json.dumps(obj, ensure_ascii=False)

        out: list[str] = []
        if thinking and reasoning_text:
            out.append(events[0])
            for piece in _split(reasoning_text):
                out.append(frame({"reasoning_content": piece}))
            out.extend(events[1:])
        else:
            out = list(events)

        out.append("data: [DONE]")
        return self.sse_response(out)


    def list_models(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        data = [
            {"id": "deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek-v4-pro", "object": "model", "owned_by": "deepseek"},
        ]
        return self.json_response({"object": "list", "data": data})


    def user_balance(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        return self.json_response({
            "is_available": True,
            "balance_infos": [{
                "currency": "CNY",
                "total_balance": "110.00",
                "granted_balance": "10.00",
                "topped_up_balance": "100.00",
            }],
        })


def _split(text: str, n: int = 3) -> list[str]:
    words = text.split(" ")
    if len(words) <= n:
        return words
    size = max(1, len(words) // n)
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i:i + size]))
        i += size
    return [out[0]] + [" " + p for p in out[1:]]
