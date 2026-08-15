# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from flask import Flask, request

from ..core import ProviderServer
from ..content import ContentPolicy, estimate_tokens
from . import _openai_compat as oai

_CHAT_MODELS = {
    "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.2",
    "gpt-5.2-chat-latest", "gpt-5.2-pro", "gpt-5.1", "gpt-5", "gpt-5-mini",
    "gpt-5-nano",
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-4.1", "gpt-4.1-mini",
    "gpt-3.5-turbo",
}
_EMBED_MODELS = {"text-embedding-3-small", "text-embedding-3-large"}
_SYSTEM_FINGERPRINT = "fp_44709d6fcb"


def _is_reasoning_model(model: str) -> bool:
    return model.startswith("gpt-5")


class OpenAIServer(ProviderServer):
    name = "openai"
    default_port = 8001

    def __init__(self, answer: str | None = None, thinking: str | None = None):
        self.content = ContentPolicy(answer, thinking)
        super().__init__()


    def register_routes(self, app: Flask) -> None:
        app.add_url_rule("/v1/chat/completions", "chat",
                         self.chat_completions, methods=["POST"])
        app.add_url_rule("/v1/responses", "responses",
                         self.responses, methods=["POST"])
        app.add_url_rule("/v1/models", "models", self.list_models, methods=["GET"])
        app.add_url_rule("/v1/models/<path:model>", "model", self.get_model,
                         methods=["GET"])
        app.add_url_rule("/v1/embeddings", "embeddings", self.embeddings,
                         methods=["POST"])

    def _decorate(self, resp) -> None:
        resp.headers["x-request-id"] = "req_" + oai.rand_suffix(24, "0123456789abcdef")
        resp.headers["openai-version"] = "2020-10-01"
        resp.headers["openai-processing-ms"] = "42"
        resp.headers["x-ratelimit-limit-requests"] = "10000"
        resp.headers["x-ratelimit-remaining-requests"] = "9999"
        resp.headers["x-ratelimit-reset-requests"] = "6m0s"
        resp.headers["x-ratelimit-limit-tokens"] = "2000000"
        resp.headers["x-ratelimit-remaining-tokens"] = "1999000"


    def _auth_error(self):
        header = request.headers.get("Authorization")
        if not header:
            return self.json_response(oai.oai_error(
                "You didn't provide an API key. You need to provide your API "
                "key in an Authorization header using Bearer auth (i.e. "
                "Authorization: Bearer YOUR_KEY).",
                type="invalid_request_error", code=None,
            ), status=401)
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not token.startswith("sk-"):
            shown = (token[:6] + "***" + token[-4:]) if len(token) > 10 else "***"
            return self.json_response(oai.oai_error(
                f"Incorrect API key provided: {shown}. You can find your API "
                "key at https://platform.openai.com/account/api-keys.",
                type="invalid_request_error", code="invalid_api_key",
            ), status=401)
        return None


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
                code="invalid_json"), status=400)
        body = rec.json

        if "model" not in body:
            return self.json_response(oai.oai_error(
                "Missing required parameter: 'model'.", param="model",
                code="missing_required_parameter"), status=400)
        model = body["model"]

        if model not in _CHAT_MODELS:
            return self.json_response(oai.oai_error(
                f"The model `{model}` does not exist or you do not have "
                "access to it.", code="model_not_found"), status=404)

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self.json_response(oai.oai_error(
                "Missing required parameter: 'messages'.", param="messages",
                code="missing_required_parameter"), status=400)

        if _is_reasoning_model(model):
            if "max_tokens" in body:
                return self.json_response(oai.oai_error(
                    "Unsupported parameter: 'max_tokens' is not supported with "
                    "this model. Use 'max_completion_tokens' instead.",
                    param="max_tokens", code="unsupported_parameter"), status=400)
            for p in ("temperature", "top_p"):
                if p in body and body[p] not in (1, 1.0):
                    return self.json_response(oai.oai_error(
                        f"Unsupported value: '{p}' does not support "
                        f"{body[p]} with this model. Only the default (1) "
                        "value is supported.", param=p,
                        code="unsupported_value"), status=400)
        if "temperature" in body and isinstance(body["temperature"], (int, float)) and body["temperature"] > 2:
            return self.json_response(oai.oai_error(
                "Invalid 'temperature': decimal above maximum value. "
                f"Expected a value <= 2, but got {body['temperature']} "
                "instead.", param="temperature",
                code="decimal_above_max_value"), status=400)

        text = self.content.answer(body)
        prompt_text = oai.flatten_prompt_text(messages)
        reasoning = (estimate_tokens(self.content.thinking(body))
                     if _is_reasoning_model(model) else 0)

        if body.get("stream"):
            include_usage = bool(
                (body.get("stream_options") or {}).get("include_usage"))
            events = oai.chat_completion_sse_events(
                model, text, system_fingerprint=_SYSTEM_FINGERPRINT,
                include_usage=include_usage, prompt_text=prompt_text,
                reasoning_tokens=reasoning)
            return self.sse_response(events)

        return self.json_response(oai.chat_completion_body(
            model, text, prompt_text=prompt_text,
            system_fingerprint=_SYSTEM_FINGERPRINT, reasoning_tokens=reasoning))


    def responses(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        if not rec.json_ok or "model" not in (rec.json or {}):
            return self.json_response(oai.oai_error(
                "Missing required parameter: 'model'.", param="model",
                code="missing_required_parameter"), status=400)
        body = rec.json
        model = body["model"]
        if model not in _CHAT_MODELS:
            return self.json_response(oai.oai_error(
                f"The model `{model}` does not exist or you do not have "
                "access to it.", code="model_not_found"), status=404)

        text = self.content.answer(body)
        reasoning = estimate_tokens(self.content.thinking(body))
        out_tokens = estimate_tokens(text) + reasoning
        obj = {
            "id": "resp_" + oai.rand_suffix(32, "0123456789abcdef"),
            "object": "response",
            "created_at": 1751700000,
            "status": "completed",
            "model": model,
            "output": [
                {"id": "rs_" + oai.rand_suffix(16), "type": "reasoning",
                 "summary": []},
                {"id": "msg_" + oai.rand_suffix(16), "type": "message",
                 "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": text,
                              "annotations": []}]},
            ],
            "reasoning": {"effort": "medium", "summary": None},
            "text": {"format": {"type": "text"}, "verbosity": "medium"},
            "usage": {
                "input_tokens": 36, "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": out_tokens,
                "output_tokens_details": {"reasoning_tokens": reasoning},
                "total_tokens": 36 + out_tokens,
            },
            "store": True, "temperature": 1.0, "top_p": 1.0,
            "tool_choice": "auto", "tools": [], "metadata": {},
        }
        return self.json_response(obj)


    def list_models(self):
        _rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        data = [{"id": m, "object": "model", "created": 1686935002,
                 "owned_by": "openai"}
                for m in sorted(_CHAT_MODELS | _EMBED_MODELS)]
        return self.json_response({"object": "list", "data": data})

    def get_model(self, model: str):
        _rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        if model not in _CHAT_MODELS and model not in _EMBED_MODELS:
            return self.json_response(oai.oai_error(
                f"The model '{model}' does not exist", code="model_not_found"),
                status=404)
        return self.json_response({"id": model, "object": "model",
                                   "created": 1686935002, "owned_by": "openai"})


    def embeddings(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._auth_error()
        if auth is not None:
            return auth
        body = rec.json if rec.json_ok else {}
        model = body.get("model")
        if model not in _EMBED_MODELS:
            return self.json_response(oai.oai_error(
                f"The model `{model}` does not exist or you do not have "
                "access to it.", code="model_not_found"), status=404)
        inputs = body.get("input")
        items = inputs if isinstance(inputs, list) else [inputs]
        data = [{"object": "embedding", "index": i,
                 "embedding": [0.0, 0.1, 0.2, 0.3]}
                for i, _ in enumerate(items)]
        return self.json_response({
            "object": "list", "data": data, "model": model,
            "usage": {"prompt_tokens": 8, "total_tokens": 8},
        })
