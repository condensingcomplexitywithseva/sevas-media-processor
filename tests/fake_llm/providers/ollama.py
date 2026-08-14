# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import random
import time
from datetime import datetime, timezone

from flask import Flask, request

from ..core import ProviderServer
from ..content import ContentPolicy, estimate_tokens
from . import _openai_compat as oai

_SYSTEM_FINGERPRINT = "fp_ollama"

_MODELS = {
    "qwen3.5:0.8b",
    "qwen3",
    "qwen3:30b",
    "deepseek-r1:70b",
    "gpt-oss:20b",
    "gemma3",
    "llama3.1",
    "llama3.3",
    "nomic-embed-text",
}
_EMBED_MODELS = {"nomic-embed-text"}

_TAG_DETAILS = {
    "format": "gguf",
    "family": "qwen2",
    "parameter_size": "7.6B",
    "quantization_level": "Q4_K_M",
}

_THINK_LEVELS = {"low", "medium", "high", "max"}


def _normalize(model: str) -> str:
    if not isinstance(model, str):
        return ""
    return model if ":" in model else f"{model}:latest"


def _is_known(model: str) -> bool:
    if not isinstance(model, str) or not model:
        return False
    if model in _MODELS:
        return True
    norm = _normalize(model)
    if norm in _MODELS:
        return True
    if norm.endswith(":latest") and norm[: -len(":latest")] in _MODELS:
        return True
    return False


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _wants_think(body: dict) -> bool:
    think = body.get("think")
    if think is True:
        return True
    if isinstance(think, str) and think in _THINK_LEVELS:
        return True
    return False


class OllamaServer(ProviderServer):
    name = "ollama"
    default_port = 8006

    def __init__(self, answer: str | None = None, thinking: str | None = None):
        self.content = ContentPolicy(answer, thinking)
        super().__init__()


    def register_routes(self, app: Flask) -> None:
        app.add_url_rule("/api/chat", "api_chat", self.api_chat, methods=["POST"])
        app.add_url_rule("/api/generate", "api_generate", self.api_generate,
                         methods=["POST"])
        app.add_url_rule("/api/tags", "api_tags", self.api_tags, methods=["GET"])
        app.add_url_rule("/api/show", "api_show", self.api_show, methods=["POST"])
        app.add_url_rule("/api/version", "api_version", self.api_version,
                         methods=["GET"])
        app.add_url_rule("/api/ps", "api_ps", self.api_ps, methods=["GET"])
        app.add_url_rule("/api/embed", "api_embed", self.api_embed,
                         methods=["POST"])

        app.add_url_rule("/v1/chat/completions", "v1_chat",
                         self.v1_chat_completions, methods=["POST"])
        app.add_url_rule("/v1/completions", "v1_completions",
                         self.v1_completions, methods=["POST"])
        app.add_url_rule("/v1/models", "v1_models", self.v1_models,
                         methods=["GET"])
        app.add_url_rule("/v1/models/<path:model>", "v1_model",
                         self.v1_get_model, methods=["GET"])
        app.add_url_rule("/v1/embeddings", "v1_embeddings",
                         self.v1_embeddings, methods=["POST"])


    def _native_error(self, message: str, status: int):
        return self.json_response({"error": message}, status=status)

    def _stats(self, prompt_text: str, text: str, thinking_text: str = ""):
        prompt_tokens = estimate_tokens(prompt_text) or 11
        eval_tokens = estimate_tokens(text) + (
            estimate_tokens(thinking_text) if thinking_text else 0)
        return {
            "total_duration": 174560334,
            "load_duration": 101397084,
            "prompt_eval_count": prompt_tokens,
            "prompt_eval_duration": 13074791,
            "eval_count": eval_tokens,
            "eval_duration": 52479709,
        }

    def _native_prompt_text(self, body: dict) -> str:
        if isinstance(body.get("messages"), list):
            return oai.flatten_prompt_text(body["messages"])
        prompt = body.get("prompt")
        return prompt if isinstance(prompt, str) else ""


    def api_chat(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        if not rec.json_ok or not isinstance(rec.json, dict):
            return self._native_error("model is required", 400)
        body = rec.json

        if "model" not in body or not body.get("model"):
            return self._native_error("model is required", 400)
        model = body["model"]

        if not _is_known(model):
            return self._native_error(
                f"model '{model}' not found, try pulling it first", 404)

        messages = body.get("messages")
        if isinstance(messages, list) and len(messages) == 0:
            done_reason = "unload" if body.get("keep_alive") == 0 else "load"
            return self._native_chat_final(
                model, "", "", "", done_reason=done_reason)

        text = self.content.answer(body)
        prompt_text = self._native_prompt_text(body)
        think = _wants_think(body)
        thinking_text = self.content.thinking(body) if think else ""

        stream = body.get("stream", True)
        if stream:
            return self._native_chat_stream(model, text, prompt_text,
                                             thinking_text)
        return self._native_chat_final(model, text, thinking_text, prompt_text)

    def _native_chat_final(self, model, text, thinking_text, prompt_text,
                           done_reason="stop"):
        message = {"role": "assistant", "content": text}
        if thinking_text:
            message["thinking"] = thinking_text
        obj = {
            "model": model,
            "created_at": _iso_now(),
            "message": message,
            "done": True,
            "done_reason": done_reason,
        }
        obj.update(self._stats(prompt_text, text, thinking_text))
        return self.json_response(obj)

    def _native_chat_stream(self, model, text, prompt_text, thinking_text):
        def lines():
            if thinking_text:
                for piece in _split(thinking_text):
                    yield {
                        "model": model,
                        "created_at": _iso_now(),
                        "message": {"role": "assistant", "content": "",
                                    "thinking": piece},
                        "done": False,
                    }
            for piece in _split(text):
                yield {
                    "model": model,
                    "created_at": _iso_now(),
                    "message": {"role": "assistant", "content": piece},
                    "done": False,
                }
            final = {
                "model": model,
                "created_at": _iso_now(),
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
            }
            final.update(self._stats(prompt_text, text, thinking_text))
            yield final

        return self.ndjson_response(lines())


    def api_generate(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        if not rec.json_ok or not isinstance(rec.json, dict):
            return self._native_error("model is required", 400)
        body = rec.json

        if "model" not in body or not body.get("model"):
            return self._native_error("model is required", 400)
        model = body["model"]

        if not _is_known(model):
            return self._native_error(
                f"model '{model}' not found, try pulling it first", 404)

        text = self.content.answer(body)
        prompt_text = body.get("prompt") if isinstance(body.get("prompt"), str) else ""
        think = _wants_think(body)
        thinking_text = self.content.thinking(body) if think else ""

        stream = body.get("stream", True)
        if stream:
            return self._native_generate_stream(model, text, prompt_text,
                                                 thinking_text)
        obj = {
            "model": model,
            "created_at": _iso_now(),
            "response": text,
            "done": True,
            "done_reason": "stop",
        }
        if thinking_text:
            obj["thinking"] = thinking_text
        obj.update(self._stats(prompt_text, text, thinking_text))
        return self.json_response(obj)

    def _native_generate_stream(self, model, text, prompt_text, thinking_text):
        def lines():
            if thinking_text:
                for piece in _split(thinking_text):
                    yield {
                        "model": model, "created_at": _iso_now(),
                        "response": "", "thinking": piece, "done": False,
                    }
            for piece in _split(text):
                yield {
                    "model": model, "created_at": _iso_now(),
                    "response": piece, "done": False,
                }
            final = {
                "model": model, "created_at": _iso_now(),
                "response": "", "done": True, "done_reason": "stop",
            }
            final.update(self._stats(prompt_text, text, thinking_text))
            yield final

        return self.ndjson_response(lines())


    def api_tags(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        models = []
        for name in sorted(_MODELS):
            models.append({
                "name": name,
                "model": name,
                "modified_at": "2026-06-01T12:00:00.000000000Z",
                "size": 4683075271,
                "digest": "0a8c" + "".join(
                    random.choice("0123456789abcdef") for _ in range(60)),
                "details": dict(_TAG_DETAILS),
            })
        return self.json_response({"models": models})


    def api_show(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        body = rec.json if rec.json_ok and isinstance(rec.json, dict) else {}
        model = body.get("model") or body.get("name")
        if not model:
            return self._native_error("model is required", 400)
        if not _is_known(model):
            return self._native_error(
                f"model '{model}' not found, try pulling it first", 404)
        return self.json_response({
            "license": "Apache License 2.0",
            "modelfile": f"# Modelfile for {model}\nFROM {model}",
            "parameters": "stop \"<|im_end|>\"",
            "template": "{{ .Prompt }}",
            "details": dict(_TAG_DETAILS),
            "model_info": {
                "general.architecture": "qwen2",
                "general.parameter_count": 7600000000,
            },
            "capabilities": ["completion", "thinking"],
        })


    def api_version(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        return self.json_response({"version": "0.5.0"})


    def api_ps(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        return self.json_response({
            "models": [{
                "name": "qwen3.5:0.8b",
                "model": "qwen3.5:0.8b",
                "size": 4683075271,
                "digest": "0a8c" + "0" * 60,
                "details": dict(_TAG_DETAILS),
                "expires_at": "2026-07-05T12:05:00.000000Z",
                "size_vram": 4683075271,
            }],
        })


    def api_embed(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        body = rec.json if rec.json_ok and isinstance(rec.json, dict) else {}
        model = body.get("model")
        if not model:
            return self._native_error("model is required", 400)
        if not _is_known(model):
            return self._native_error(
                f"model '{model}' not found, try pulling it first", 404)
        inp = body.get("input")
        items = inp if isinstance(inp, list) else [inp]
        embeddings = [[0.0, 0.1, 0.2, 0.3] for _ in items]
        return self.json_response({
            "model": model,
            "embeddings": embeddings,
            "total_duration": 14143917,
            "load_duration": 9506583,
            "prompt_eval_count": len(items),
        })


    def _v1_error(self, message: str, status: int):
        type_ = {
            400: "invalid_request_error",
            404: "not_found_error",
            500: "api_error",
        }.get(status, "api_error")
        return self.json_response(
            oai.oai_error(message, type=type_, param=None, code=None),
            status=status)

    def v1_chat_completions(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        if not rec.json_ok or not isinstance(rec.json, dict):
            return self._v1_error(
                "We could not parse the JSON body of your request.", 400)
        body = rec.json

        if "model" not in body or not body.get("model"):
            return self._v1_error("model is required", 400)
        model = body["model"]

        if not _is_known(model):
            return self._v1_error(
                f'model "{model}" not found, try pulling it first', 404)

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self._v1_error(
                "[] is too short - 'messages'", 400)

        text = self.content.answer(body)
        prompt_text = oai.flatten_prompt_text(messages)

        if body.get("stream"):
            include_usage = bool(
                (body.get("stream_options") or {}).get("include_usage"))
            events = oai.chat_completion_sse_events(
                model, text, id_prefix="chatcmpl-",
                system_fingerprint=_SYSTEM_FINGERPRINT,
                include_usage=include_usage, prompt_text=prompt_text)
            return self.sse_response(events)

        return self.json_response(oai.chat_completion_body(
            model, text, prompt_text=prompt_text, id_prefix="chatcmpl-",
            system_fingerprint=_SYSTEM_FINGERPRINT,
            include_usage_details=False))

    def v1_completions(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        if not rec.json_ok or not isinstance(rec.json, dict):
            return self._v1_error(
                "We could not parse the JSON body of your request.", 400)
        body = rec.json
        if "model" not in body or not body.get("model"):
            return self._v1_error("model is required", 400)
        model = body["model"]
        if not _is_known(model):
            return self._v1_error(
                f'model "{model}" not found, try pulling it first', 404)

        text = self.content.answer(body)
        prompt = body.get("prompt")
        prompt_text = prompt if isinstance(prompt, str) else ""
        prompt_tokens = estimate_tokens(prompt_text) or 16
        completion_tokens = estimate_tokens(text)

        if body.get("stream"):
            import json as _json
            cid = "cmpl-" + "".join(
                random.choice("0123456789") for _ in range(3))

            def events():
                for piece in _split(text):
                    yield "data: " + _json.dumps({
                        "id": cid, "object": "text_completion",
                        "created": 1751712000, "model": model,
                        "choices": [{"text": piece, "index": 0,
                                     "logprobs": None, "finish_reason": None}],
                    })
                yield "data: " + _json.dumps({
                    "id": cid, "object": "text_completion",
                    "created": 1751712000, "model": model,
                    "choices": [{"text": "", "index": 0, "logprobs": None,
                                 "finish_reason": "stop"}],
                })
                yield "data: [DONE]"

            return self.sse_response(events())

        return self.json_response({
            "id": "cmpl-" + "".join(random.choice("0123456789") for _ in range(3)),
            "object": "text_completion",
            "created": 1751712000,
            "model": model,
            "system_fingerprint": _SYSTEM_FINGERPRINT,
            "choices": [{"text": text, "index": 0, "logprobs": None,
                         "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        })

    def v1_models(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        data = [{"id": m, "object": "model", "created": 1751712000,
                 "owned_by": "library"} for m in sorted(_MODELS)]
        return self.json_response({"object": "list", "data": data})

    def v1_get_model(self, model: str):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        if not _is_known(model):
            return self._v1_error(
                f'model "{model}" not found, try pulling it first', 404)
        return self.json_response({
            "id": model, "object": "model", "created": 1751712000,
            "owned_by": "library",
        })

    def v1_embeddings(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        body = rec.json if rec.json_ok and isinstance(rec.json, dict) else {}
        model = body.get("model")
        if not model:
            return self._v1_error("model is required", 400)
        if not _is_known(model):
            return self._v1_error(
                f'model "{model}" not found, try pulling it first', 404)
        inputs = body.get("input")
        items = inputs if isinstance(inputs, list) else [inputs]
        data = [{"object": "embedding", "index": i,
                 "embedding": [0.0, 0.1, 0.2, 0.3]}
                for i, _ in enumerate(items)]
        return self.json_response({
            "object": "list", "data": data, "model": model,
            "usage": {"prompt_tokens": 8, "total_tokens": 8},
        })


    def handle_unknown_route(self):
        return self.json_response({"error": "404 page not found"}, status=404)


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
