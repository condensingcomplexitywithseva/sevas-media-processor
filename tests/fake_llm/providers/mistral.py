# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from flask import Flask, request

from ..core import ProviderServer
from ..content import ContentPolicy, estimate_tokens
from . import _openai_compat as oai

_CHAT_MODELS = {
    "mistral-large-latest",
    "mistral-medium-latest",
    "mistral-medium-2604",
    "mistral-large-2512",
    "mistral-small-latest",
    "magistral-medium-latest",
    "magistral-small-latest",
    "codestral-latest",
}
_EMBED_MODELS = {"mistral-embed"}
_ALL_MODELS = _CHAT_MODELS | _EMBED_MODELS

_ALLOWED_FIELDS = {
    "model", "messages", "temperature", "top_p", "max_tokens", "stream",
    "random_seed", "safe_prompt", "prefix", "response_format",
    "tools", "tool_choice", "reasoning_effort", "stop", "presence_penalty",
    "frequency_penalty", "n",
    "fake_answer", "fake_thinking",
}

_REASONING_ON = {"low", "medium", "high"}
_ID_PREFIX = "cmpl-"


def _req_id() -> str:
    return oai.rand_suffix(32, "0123456789abcdef")


class MistralServer(ProviderServer):
    name = "mistral"
    default_port = 8005

    def __init__(self, answer: str | None = None, thinking: str | None = None):
        self.content = ContentPolicy(answer, thinking)
        super().__init__()


    def register_routes(self, app: Flask) -> None:
        app.add_url_rule("/v1/chat/completions", "chat",
                         self.chat_completions, methods=["POST"])
        app.add_url_rule("/v1/fim/completions", "fim",
                         self.fim_completions, methods=["POST"])
        app.add_url_rule("/v1/models", "models", self.list_models,
                         methods=["GET"])
        app.add_url_rule("/v1/models/<path:model>", "model", self.get_model,
                         methods=["GET"])
        app.add_url_rule("/v1/embeddings", "embeddings", self.embeddings,
                         methods=["POST"])


    def _unauthorized(self):
        return self.json_response(
            {"message": "Unauthorized", "request_id": _req_id()}, status=401)

    def _invalid_model(self, model):
        return self.json_response({
            "object": "error",
            "message": f"Invalid model: {model}",
            "type": "invalid_model",
            "param": None,
            "code": "1500",
        }, status=400)

    def _validation_error(self, details: list[dict]):
        return self.json_response({
            "object": "error",
            "message": {"detail": details},
            "type": "invalid_request_error",
            "param": None,
            "code": None,
        }, status=422)


    def _auth_error(self):
        header = request.headers.get("Authorization")
        if not header:
            return self._unauthorized()
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not token.strip():
            return self._unauthorized()
        return None


    def chat_completions(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        auth = self._auth_error()
        if auth is not None:
            return auth

        if not rec.json_ok or not isinstance(rec.json, dict):
            return self._validation_error([
                {"type": "missing", "loc": ["body", "model"],
                 "msg": "Field required"},
            ])
        body = rec.json

        extras = [k for k in body if k not in _ALLOWED_FIELDS]
        if extras:
            details = [
                {"type": "extra_forbidden", "loc": ["body", k],
                 "msg": "Extra inputs are not permitted", "input": body[k]}
                for k in extras
            ]
            return self._validation_error(details)

        missing = [f for f in ("model", "messages") if f not in body]
        if missing:
            details = [
                {"type": "missing", "loc": ["body", f], "msg": "Field required"}
                for f in missing
            ]
            return self._validation_error(details)

        model = body["model"]
        if model not in _CHAT_MODELS:
            return self._invalid_model(model)

        messages = body["messages"]
        if not isinstance(messages, list):
            return self._validation_error([
                {"type": "list_type", "loc": ["body", "messages"],
                 "msg": "Input should be a valid list", "input": messages},
            ])

        text = self.content.answer(body)
        prompt_text = oai.flatten_prompt_text(messages)

        reasoning_effort = body.get("reasoning_effort")
        reasoning_on = (isinstance(reasoning_effort, str)
                        and reasoning_effort in _REASONING_ON)
        thinking_text = self.content.thinking(body) if reasoning_on else ""
        reasoning_tokens = estimate_tokens(thinking_text) if reasoning_on else 0

        if body.get("stream"):
            return self._stream(model, text, prompt_text, reasoning_on,
                                thinking_text)

        response = oai.chat_completion_body(
            model, text, prompt_text=prompt_text, id_prefix=_ID_PREFIX,
            include_usage_details=False, reasoning_tokens=reasoning_tokens)

        message = response["choices"][0]["message"]
        message.pop("refusal", None)
        message["tool_calls"] = None
        response["choices"][0].pop("logprobs", None)

        if reasoning_on:
            message["content"] = [
                {"type": "thinking",
                 "thinking": [{"type": "text", "text": thinking_text}]},
                {"type": "text", "text": text},
            ]

        return self.json_response(response)


    def _stream(self, model, text, prompt_text, reasoning_on, thinking_text):
        import json as _json

        cid = _ID_PREFIX + oai.rand_suffix()

        def frame(delta, fin=None, usage=None):
            obj = {
                "id": cid, "object": "chat.completion.chunk",
                "created": 1751702400, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": fin}],
            }
            if usage is not None:
                obj["usage"] = usage
            return "data: " + _json.dumps(obj, ensure_ascii=False)

        events = [frame({"role": "assistant", "content": ""})]
        if reasoning_on:
            for piece in _split(thinking_text):
                events.append(frame({"content": [
                    {"type": "thinking",
                     "thinking": [{"type": "text", "text": piece}]}]}))
            for piece in _split(text):
                events.append(frame({"content": [
                    {"type": "text", "text": piece}]}))
        else:
            for piece in _split(text):
                events.append(frame({"content": piece}))

        prompt_tokens = estimate_tokens(prompt_text) or 16
        completion_tokens = estimate_tokens(text) + (
            estimate_tokens(thinking_text) if reasoning_on else 0)
        events.append(frame({"content": ""}, fin="stop", usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }))
        events.append("data: [DONE]")
        return self.sse_response(events)


    def fim_completions(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth

        if not rec.json_ok or not isinstance(rec.json, dict):
            return self._validation_error([
                {"type": "missing", "loc": ["body", "model"],
                 "msg": "Field required"},
            ])
        body = rec.json
        if "model" not in body:
            return self._validation_error([
                {"type": "missing", "loc": ["body", "model"],
                 "msg": "Field required"},
            ])
        model = body["model"]
        if model not in _CHAT_MODELS:
            return self._invalid_model(model)

        prompt = body.get("prompt", "")
        text = self.content.answer(body)
        response = oai.chat_completion_body(
            model, text, prompt_text=str(prompt), id_prefix=_ID_PREFIX,
            include_usage_details=False)
        message = response["choices"][0]["message"]
        message.pop("refusal", None)
        message["tool_calls"] = None
        response["choices"][0].pop("logprobs", None)
        return self.json_response(response)


    def list_models(self):
        _rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        data = [
            {"id": m, "object": "model", "created": 1751700000,
             "owned_by": "mistralai"}
            for m in sorted(_ALL_MODELS)
        ]
        return self.json_response({"object": "list", "data": data})

    def get_model(self, model: str):
        _rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        if model not in _ALL_MODELS:
            return self._invalid_model(model)
        return self.json_response({
            "id": model, "object": "model", "created": 1751700000,
            "owned_by": "mistralai",
        })


    def embeddings(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth

        if not rec.json_ok or not isinstance(rec.json, dict):
            return self._validation_error([
                {"type": "missing", "loc": ["body", "model"],
                 "msg": "Field required"},
            ])
        body = rec.json
        if "model" not in body:
            return self._validation_error([
                {"type": "missing", "loc": ["body", "model"],
                 "msg": "Field required"},
            ])
        model = body["model"]
        if model not in _EMBED_MODELS:
            return self._invalid_model(model)

        inputs = body.get("input")
        items = inputs if isinstance(inputs, list) else [inputs]
        data = [
            {"object": "embedding", "index": i,
             "embedding": [0.0, 0.1, 0.2, 0.3]}
            for i, _ in enumerate(items)
        ]
        prompt_tokens = sum(
            estimate_tokens(x) for x in items if isinstance(x, str)) or 8
        return self.json_response({
            "id": "embd-" + oai.rand_suffix(32, "0123456789abcdef"),
            "object": "list",
            "data": data,
            "model": model,
            "usage": {"prompt_tokens": prompt_tokens,
                      "total_tokens": prompt_tokens},
        })


    def handle_unknown_route(self):
        return self.json_response({
            "object": "error",
            "message": "Not Found",
            "type": "invalid_request_error",
            "param": None,
            "code": None,
        }, status=404)


def _split(text: str, n: int = 4) -> list[str]:
    words = text.split(" ")
    if len(words) <= n:
        return words
    size = max(1, len(words) // n)
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i:i + size]))
        i += size
    return [out[0]] + [" " + p for p in out[1:]]
