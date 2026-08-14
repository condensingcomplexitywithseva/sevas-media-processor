# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import string

from flask import Flask, request

from ..core import ProviderServer
from ..content import ContentPolicy, estimate_tokens
from . import _openai_compat as oai

_MODELS: dict[str, dict] = {
    "qwen/qwen3.5:0.8b": {
        "type": "llm", "arch": "qwen3", "quant": "Q4_K_M", "quantization": "Q4_K_M",
        "context_length": 4096, "state": "loaded", "reasoning": True,
    },
    "qwen/qwen3-8b": {
        "type": "llm", "arch": "qwen3", "quant": "Q4_K_M", "quantization": "Q4_K_M",
        "context_length": 4096, "state": "not-loaded", "reasoning": True,
    },
    "meta-llama-3.1-8b-instruct": {
        "type": "llm", "arch": "llama", "quant": "Q4_K_M", "quantization": "Q4_K_M",
        "context_length": 8192, "state": "not-loaded", "reasoning": False,
    },
    "granite-3.0-2b-instruct": {
        "type": "llm", "arch": "granite", "quant": "Q4_K_M", "quantization": "Q4_K_M",
        "context_length": 4096, "state": "not-loaded", "reasoning": False,
    },
}

_EMBED_MODELS: dict[str, dict] = {
    "text-embedding-nomic-embed-text-v1.5": {
        "type": "embeddings", "arch": "nomic-bert", "quant": "Q4_0",
        "quantization": "Q4_0", "context_length": 2048, "state": "not-loaded",
    },
}

_LOWER_ALNUM = string.ascii_lowercase + string.digits


def _chatcmpl_id() -> str:
    return "chatcmpl-" + oai.rand_suffix(25, _LOWER_ALNUM)


def _canonical(model) -> str | None:
    if not isinstance(model, str):
        return None
    name = model.split("@", 1)[0]
    if name in _MODELS or name in _EMBED_MODELS:
        return name
    return None


def _reasoning_requested(model_meta: dict | None, body: dict) -> bool:
    if model_meta and model_meta.get("reasoning"):
        return True
    if "reasoning_effort" in body:
        return True
    r = body.get("reasoning")
    if isinstance(r, dict) or isinstance(r, str):
        return True
    return False


class LMStudioServer(ProviderServer):
    name = "lm-studio"
    default_port = 8007

    def __init__(self, answer: str | None = None, thinking: str | None = None):
        self.content = ContentPolicy(answer, thinking)
        super().__init__()


    def register_routes(self, app: Flask) -> None:
        app.add_url_rule("/v1/chat/completions", "chat",
                         self.chat_completions, methods=["POST"])
        app.add_url_rule("/v1/completions", "completions",
                         self.completions, methods=["POST"])
        app.add_url_rule("/v1/embeddings", "embeddings",
                         self.embeddings, methods=["POST"])
        app.add_url_rule("/v1/responses", "responses",
                         self.responses, methods=["POST"])
        app.add_url_rule("/v1/models", "models", self.list_models,
                         methods=["GET"])

        app.add_url_rule("/api/v0/chat/completions", "v0_chat",
                         self.chat_completions_v0, methods=["POST"])
        app.add_url_rule("/api/v0/completions", "v0_completions",
                         self.completions, methods=["POST"])
        app.add_url_rule("/api/v0/embeddings", "v0_embeddings",
                         self.embeddings, methods=["POST"])
        app.add_url_rule("/api/v0/models", "v0_models", self.list_models_v0,
                         methods=["GET"])
        app.add_url_rule("/api/v0/models/<path:model>", "v0_model",
                         self.get_model_v0, methods=["GET"])


    def _decorate(self, resp) -> None:
        resp.headers["X-Powered-By"] = "Express"

    def handle_unknown_route(self):
        return self.json_response(
            {"error": f"Unexpected endpoint or method. "
                      f"({request.method} {request.path})"},
            status=200,
        )


    def _no_model_error(self, model=None):
        if model:
            msg = (f'Model "{model}" not found. Please make sure the model is '
                   f"downloaded and available, or load it in the developer page "
                   f"or use the `lms load` command.")
        else:
            msg = ("No models loaded. Please load a model in the developer page "
                   "or use the `lms load` command.")
        return self.json_response({"error": msg}, status=404)


    def _validate_chat(self, rec):
        if not rec.json_ok or not isinstance(rec.json, dict):
            return None, None, self.json_response(oai.oai_error(
                "Failed to parse the request body as JSON.",
                code="invalid_request_error"), status=400)
        body = rec.json

        if "model" not in body:
            return body, None, self._no_model_error()

        model_id = _canonical(body["model"])
        if model_id is None or model_id in _EMBED_MODELS:
            return body, None, self._no_model_error(body["model"])

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return body, model_id, self.json_response(oai.oai_error(
                "Missing required parameter: 'messages'.", param="messages",
                code="invalid_request_error"), status=400)

        return body, model_id, None

    def _build_chat(self, body: dict, model_id: str):
        meta = _MODELS.get(model_id, {})
        text = self.content.answer(body)
        prompt_text = oai.flatten_prompt_text(body.get("messages"))

        want_reasoning = _reasoning_requested(meta, body)
        reasoning_text = self.content.thinking(body) if want_reasoning else ""
        reasoning_tokens = estimate_tokens(reasoning_text) if want_reasoning else 0

        message_extra = None
        if want_reasoning:
            message_extra = {
                "reasoning": reasoning_text,
                "reasoning_content": reasoning_text,
            }

        obj = oai.chat_completion_body(
            model_id, text, prompt_text=prompt_text, id=_chatcmpl_id(),
            reasoning_tokens=reasoning_tokens, message_extra=message_extra)

        mt = body.get("max_tokens")
        stop_reason = "eosFound"
        if isinstance(mt, int) and mt > 0 and estimate_tokens(text) >= mt:
            stop_reason = "maxPredictedTokensReached"
            obj["choices"][0]["finish_reason"] = "length"
        return obj, stop_reason

    def _stream_chat(self, body: dict, model_id: str):
        import json as _json

        meta = _MODELS.get(model_id, {})
        text = self.content.answer(body)
        prompt_text = oai.flatten_prompt_text(body.get("messages"))
        want_reasoning = _reasoning_requested(meta, body)
        reasoning_text = self.content.thinking(body) if want_reasoning else ""
        reasoning_tokens = estimate_tokens(reasoning_text) if want_reasoning else 0

        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

        cid = _chatcmpl_id()
        base = oai.chat_completion_sse_events(
            model_id, text, id=cid, include_usage=include_usage,
            prompt_text=prompt_text, reasoning_tokens=reasoning_tokens,
            done=False)

        first = _json.loads(base[0][len("data: "):])
        cts = first["created"]

        def frame(delta):
            obj = {
                "id": cid, "object": "chat.completion.chunk", "created": cts,
                "model": model_id,
                "choices": [{"index": 0, "delta": delta,
                             "logprobs": None, "finish_reason": None}],
            }
            return "data: " + _json.dumps(obj, ensure_ascii=False)

        out: list[str] = []
        if want_reasoning and reasoning_text:
            out.append(base[0])
            for piece in _split(reasoning_text):
                out.append(frame({"reasoning": piece}))
            out.extend(base[1:])
        else:
            out = list(base)

        out.append("data: [DONE]")
        return self.sse_response(out)


    def chat_completions(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        body, model_id, err = self._validate_chat(rec)
        if err is not None:
            return err

        if body.get("stream"):
            return self._stream_chat(body, model_id)

        obj, _stop = self._build_chat(body, model_id)
        return self.json_response(obj)


    def chat_completions_v0(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        body, model_id, err = self._validate_chat(rec)
        if err is not None:
            return err

        if body.get("stream"):
            return self._stream_chat(body, model_id)

        obj, stop_reason = self._build_chat(body, model_id)
        meta = _MODELS.get(model_id, {})
        obj["stats"] = {
            "tokens_per_second": 51.43,
            "time_to_first_token": 0.111,
            "generation_time": 0.954,
            "stop_reason": stop_reason,
        }
        obj["model_info"] = {
            "arch": meta.get("arch", "granite"),
            "quant": meta.get("quant", "Q4_K_M"),
            "format": "gguf",
            "context_length": meta.get("context_length", 4096),
        }
        obj["runtime"] = {
            "name": "llama.cpp-win-x86_64-avx2",
            "version": "1.3.0",
            "supported_formats": ["gguf"],
        }
        return self.json_response(obj)


    def completions(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        if not rec.json_ok or not isinstance(rec.json, dict):
            return self.json_response(oai.oai_error(
                "Failed to parse the request body as JSON.",
                code="invalid_request_error"), status=400)
        body = rec.json
        model_id = _canonical(body.get("model"))
        if model_id is None or model_id in _EMBED_MODELS:
            return self._no_model_error(body.get("model"))

        prompt = body.get("prompt")
        text = self.content.answer(body)
        prompt_text = prompt if isinstance(prompt, str) else ""
        prompt_tokens = estimate_tokens(prompt_text) or 16
        completion_tokens = estimate_tokens(text)
        obj = {
            "id": "cmpl-" + oai.rand_suffix(25, _LOWER_ALNUM),
            "object": "text_completion",
            "created": 1751700000,
            "model": model_id,
            "choices": [{
                "index": 0, "text": text, "logprobs": None,
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        return self.json_response(obj)


    def responses(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        if not rec.json_ok or not isinstance(rec.json, dict):
            return self.json_response(oai.oai_error(
                "Failed to parse the request body as JSON.",
                code="invalid_request_error"), status=400)
        body = rec.json
        model_id = _canonical(body.get("model"))
        if model_id is None or model_id in _EMBED_MODELS:
            return self._no_model_error(body.get("model"))

        text = self.content.answer(body)
        out_tokens = estimate_tokens(text)
        obj = {
            "id": "resp_" + oai.rand_suffix(32, "0123456789abcdef"),
            "object": "response",
            "created_at": 1751700000,
            "status": "completed",
            "model": model_id,
            "output": [{
                "id": "msg_" + oai.rand_suffix(16, _LOWER_ALNUM),
                "type": "message", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": text,
                             "annotations": []}],
            }],
            "usage": {
                "input_tokens": 16, "output_tokens": out_tokens,
                "total_tokens": 16 + out_tokens,
            },
        }
        return self.json_response(obj)


    def embeddings(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        body = rec.json if rec.json_ok and isinstance(rec.json, dict) else {}
        model = body.get("model")
        model_id = _canonical(model)
        if model_id is None:
            return self._no_model_error(model)
        inputs = body.get("input")
        items = inputs if isinstance(inputs, list) else [inputs]
        data = [{"object": "embedding", "index": i,
                 "embedding": [0.0, 0.1, 0.2, 0.3]}
                for i, _ in enumerate(items)]
        prompt_tokens = 8 * max(1, len(items))
        return self.json_response({
            "object": "list", "data": data, "model": model_id,
            "usage": {"prompt_tokens": prompt_tokens,
                      "total_tokens": prompt_tokens},
        })


    def list_models(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        data = [{"id": mid, "object": "model", "owned_by": "organization_owner"}
                for mid in list(_MODELS) + list(_EMBED_MODELS)]
        return self.json_response({"object": "list", "data": data})


    def _v0_model_entry(self, mid: str, meta: dict) -> dict:
        return {
            "id": mid,
            "object": "model",
            "type": meta.get("type", "llm"),
            "publisher": mid.split("/", 1)[0] if "/" in mid else "lmstudio",
            "arch": meta.get("arch", "llama"),
            "compatibility_type": "gguf",
            "quantization": meta.get("quantization", "Q4_K_M"),
            "state": meta.get("state", "not-loaded"),
            "max_context_length": meta.get("context_length", 4096),
        }

    def list_models_v0(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        data = [self._v0_model_entry(mid, meta)
                for mid, meta in list(_MODELS.items()) + list(_EMBED_MODELS.items())]
        return self.json_response({"object": "list", "data": data})

    def get_model_v0(self, model: str):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        mid = _canonical(model)
        if mid is None:
            return self._no_model_error(model)
        meta = _MODELS.get(mid) or _EMBED_MODELS.get(mid, {})
        return self.json_response(self._v0_model_entry(mid, meta))


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
