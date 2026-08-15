# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from flask import Flask, request

from ..core import ProviderServer
from ..content import ContentPolicy, estimate_tokens
from . import _openai_compat as oai

_MODELS = {
    "gemini-2.0-flash",
    "gemini-3.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-pro-latest",
}

_GENERATE_ACTIONS = {"generateContent", "streamGenerateContent"}

_REQUEST_FIELDS = {
    "contents", "tools", "toolConfig", "safetySettings", "systemInstruction",
    "generationConfig", "cachedContent",
    "fakeAnswer", "fakeThinking",
}

_GENERATION_CONFIG_FIELDS = {
    "stopSequences", "responseMimeType", "responseSchema",
    "responseJsonSchema", "responseModalities", "candidateCount",
    "maxOutputTokens", "temperature", "topP", "topK", "seed",
    "presencePenalty", "frequencyPenalty", "responseLogprobs", "logprobs",
    "enableEnhancedCivicAnswers", "speechConfig", "thinkingConfig",
    "mediaResolution", "audioTimestamp",
}


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest if part)


def _strip_model_prefix(model: str) -> str:
    return model[len("models/"):] if model.startswith("models/") else model


class GeminiServer(ProviderServer):
    name = "gemini"
    default_port = 8003

    def __init__(self, answer: str | None = None, thinking: str | None = None):
        self.content = ContentPolicy(answer, thinking)
        super().__init__()


    def register_routes(self, app: Flask) -> None:
        app.add_url_rule("/v1beta/openai/chat/completions", "gemini_oai_chat",
                         self.oai_chat_completions, methods=["POST"])
        app.add_url_rule("/v1beta/openai/models", "gemini_oai_models",
                         self.oai_list_models, methods=["GET"])

        app.add_url_rule("/v1beta/models", "gemini_list_models",
                         self.list_models, methods=["GET"])

        app.add_url_rule("/v1beta/models/<path:model_and_action>",
                         "gemini_custom_method", self.custom_method,
                         methods=["POST"])

    def _decorate(self, resp) -> None:
        resp.headers["x-goog-api-client"] = "fake-gemini/1"
        resp.headers["server"] = "scaffolding on HTTPServer2"


    def _api_key(self) -> str | None:
        header = request.headers.get("x-goog-api-key")
        if header:
            return header
        return request.args.get("key")

    def _native_auth_error(self):
        key = self._api_key()
        if not key:
            return self._error(
                403,
                "Method doesn't allow unregistered callers (callers without "
                "established identity). Please use API Key or other form of "
                "API consumer identity to call this API.",
                "PERMISSION_DENIED",
            )
        if not key.startswith("AIza"):
            return self._error(
                400,
                "API key not valid. Please pass a valid API key.",
                "INVALID_ARGUMENT",
                details=[{
                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                    "reason": "API_KEY_INVALID",
                    "domain": "googleapis.com",
                    "metadata": {"service": "generativelanguage.googleapis.com"},
                }],
            )
        return None

    def _error(self, code: int, message: str, status: str, details=None):
        err = {"code": code, "message": message, "status": status}
        if details is not None:
            err["details"] = details
        return self.json_response({"error": err}, status=code)

    def _unknown_name_error(self, key: str, at: str | None = None):
        where = f" at '{at}'" if at else ""
        message = (f'Invalid JSON payload received. Unknown name "{key}"'
                   f"{where}: Cannot find field.")
        return self._error(400, message, "INVALID_ARGUMENT", details=[{
            "@type": "type.googleapis.com/google.rpc.BadRequest",
            "fieldViolations": [{"description": message}],
        }])

    def _unknown_field_error(self, body: dict):
        for key in body:
            if _camel(key) not in _REQUEST_FIELDS:
                return self._unknown_name_error(key)
        gen = body.get("generationConfig")
        if gen is None:
            gen = body.get("generation_config")
        if isinstance(gen, dict):
            for key in gen:
                if _camel(key) not in _GENERATION_CONFIG_FIELDS:
                    return self._unknown_name_error(key, at="generation_config")
        return None


    def custom_method(self, model_and_action: str):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        if ":" not in model_and_action:
            return self._error(
                400,
                f"Invalid custom method: models/{model_and_action} is missing "
                "a ':<method>' suffix.",
                "INVALID_ARGUMENT",
            )
        model_raw, _, action = model_and_action.rpartition(":")
        model = _strip_model_prefix(model_raw)

        auth = self._native_auth_error()
        if auth is not None:
            return auth

        if action in _GENERATE_ACTIONS:
            return self._generate(rec, model, streaming=(action == "streamGenerateContent"))
        if action == "countTokens":
            return self._count_tokens(rec, model)

        return self._error(
            400,
            f"Unknown custom method: models/{model_raw}:{action}.",
            "INVALID_ARGUMENT",
        )


    def _validate_generate(self, rec, model: str):
        if model not in _MODELS:
            return self._error(
                404,
                f"models/{model} is not found for API version v1beta, or is "
                "not supported for generateContent. Call ListModels to see the "
                "list of available models and their supported methods.",
                "NOT_FOUND",
            )

        if not rec.json_ok:
            return self._error(
                400,
                "Invalid JSON payload received.",
                "INVALID_ARGUMENT",
            )
        body = rec.json if isinstance(rec.json, dict) else {}

        unknown = self._unknown_field_error(body)
        if unknown is not None:
            return unknown

        contents = body.get("contents")
        if not isinstance(contents, list) or not contents:
            return self._error(
                400,
                "* GenerateContentRequest.contents: contents is not specified.",
                "INVALID_ARGUMENT",
            )

        for content in contents:
            if not isinstance(content, dict):
                continue
            role = content.get("role")
            if role is not None and role not in ("user", "model"):
                return self._error(
                    400,
                    "Invalid JSON payload received. Unknown value at "
                    f"'contents[].role': \"{role}\". Allowed values: [user, model].",
                    "INVALID_ARGUMENT",
                )
        return None

    def _prompt_text(self, body: dict) -> str:
        out: list[str] = []
        contents = body.get("contents")
        if not isinstance(contents, list):
            return ""
        for content in contents:
            if not isinstance(content, dict):
                continue
            for part in content.get("parts", []) or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    out.append(part["text"])
        return " ".join(out)

    def _wants_thoughts(self, body: dict) -> bool:
        gen = body.get("generationConfig") or body.get("generation_config") or {}
        if not isinstance(gen, dict):
            return False
        tc = gen.get("thinkingConfig") or gen.get("thinking_config") or {}
        if not isinstance(tc, dict):
            return False
        return bool(tc.get("includeThoughts") or tc.get("include_thoughts"))

    def _candidate_parts(self, body: dict):
        answer = self.content.answer(body)
        parts = []
        thoughts_tokens = 0
        if self._wants_thoughts(body):
            thinking = self.content.thinking(body)
            thoughts_tokens = estimate_tokens(thinking)
            parts.append({"text": thinking, "thought": True})
        parts.append({"text": answer})
        return parts, answer, thoughts_tokens

    def _usage_metadata(self, prompt_text: str, answer: str, thoughts_tokens: int):
        prompt_tokens = estimate_tokens(prompt_text) or 8
        cand_tokens = estimate_tokens(answer)
        return {
            "promptTokenCount": prompt_tokens,
            "candidatesTokenCount": cand_tokens,
            "totalTokenCount": prompt_tokens + cand_tokens + thoughts_tokens,
            "thoughtsTokenCount": thoughts_tokens,
            "promptTokensDetails": [
                {"modality": "TEXT", "tokenCount": prompt_tokens}
            ],
        }

    def _generate(self, rec, model: str, streaming: bool):
        err = self._validate_generate(rec, model)
        if err is not None:
            return err
        body = rec.json if isinstance(rec.json, dict) else {}

        parts, answer, thoughts_tokens = self._candidate_parts(body)
        prompt_text = self._prompt_text(body)
        usage = self._usage_metadata(prompt_text, answer, thoughts_tokens)
        response_id = "resp-" + oai.rand_suffix(16, "0123456789abcdef")

        if not streaming:
            obj = {
                "candidates": [{
                    "content": {"parts": parts, "role": "model"},
                    "finishReason": "STOP",
                    "index": 0,
                    "safetyRatings": [
                        {"category": "HARM_CATEGORY_HATE_SPEECH",
                         "probability": "NEGLIGIBLE"},
                    ],
                }],
                "usageMetadata": usage,
                "modelVersion": model,
                "responseId": response_id,
            }
            return self.json_response(obj)

        chunk_texts = _split_words(answer)
        n = len(chunk_texts)
        chunks = []
        for i, piece in enumerate(chunk_texts):
            chunk_parts = []
            if i == 0 and thoughts_tokens:
                chunk_parts.append({"text": self.content.thinking(body),
                                    "thought": True})
            chunk_parts.append({"text": piece})
            candidate = {
                "content": {"parts": chunk_parts, "role": "model"},
                "index": 0,
            }
            if i == n - 1:
                candidate["finishReason"] = "STOP"
                candidate["safetyRatings"] = [
                    {"category": "HARM_CATEGORY_HATE_SPEECH",
                     "probability": "NEGLIGIBLE"},
                ]
            chunks.append({
                "candidates": [candidate],
                "usageMetadata": usage,
                "modelVersion": model,
                "responseId": response_id,
            })

        if request.args.get("alt") == "sse":
            import json as _json
            events = ["data: " + _json.dumps(c, ensure_ascii=False)
                      for c in chunks]
            return self.sse_response(events)

        return self.json_response(chunks)


    def _count_tokens(self, rec, model: str):
        if model not in _MODELS:
            return self._error(
                404,
                f"models/{model} is not found for API version v1beta, or is "
                "not supported for countTokens. Call ListModels to see the "
                "list of available models and their supported methods.",
                "NOT_FOUND",
            )
        body = rec.json if isinstance(rec.json, dict) else {}
        prompt_text = self._prompt_text(body)
        total = estimate_tokens(prompt_text) or 1
        return self.json_response({
            "totalTokens": total,
            "promptTokensDetails": [{"modality": "TEXT", "tokenCount": total}],
        })


    def list_models(self):
        _rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._native_auth_error()
        if auth is not None:
            return auth
        models = [{
            "name": f"models/{m}",
            "version": "001",
            "displayName": m,
            "supportedGenerationMethods": [
                "generateContent", "streamGenerateContent", "countTokens",
            ],
        } for m in sorted(_MODELS)]
        return self.json_response({"models": models})


    def _oai_auth_error(self):
        header = request.headers.get("Authorization")
        token = ""
        if header and header.lower().startswith("bearer "):
            token = header[7:]
        if not token.startswith("AIza"):
            return self.json_response(oai.oai_error(
                "API key not valid. Please pass a valid API key.",
                type="invalid_request_error", code="API_KEY_INVALID",
            ), status=400)
        return None

    def oai_chat_completions(self):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted

        auth = self._oai_auth_error()
        if auth is not None:
            return auth

        if not rec.json_ok:
            return self.json_response(oai.oai_error(
                "We could not parse the JSON body of your request.",
                code="invalid_json"), status=400)
        body = rec.json

        model = body.get("model")
        if model is None:
            return self.json_response(oai.oai_error(
                "Missing required parameter: 'model'.", param="model",
                code="missing_required_parameter"), status=400)
        if _strip_model_prefix(model) not in _MODELS:
            return self.json_response(oai.oai_error(
                f"The model `{model}` does not exist or you do not have "
                "access to it.", code="model_not_found"), status=404)

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return self.json_response(oai.oai_error(
                "Missing required parameter: 'messages'.", param="messages",
                code="missing_required_parameter"), status=400)

        text = self.content.answer(body)
        prompt_text = oai.flatten_prompt_text(messages)

        if body.get("stream"):
            include_usage = bool(
                (body.get("stream_options") or {}).get("include_usage"))
            events = oai.chat_completion_sse_events(
                model, text, include_usage=include_usage,
                prompt_text=prompt_text)
            return self.sse_response(events)

        return self.json_response(oai.chat_completion_body(
            model, text, prompt_text=prompt_text))

    def oai_list_models(self):
        _rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        auth = self._oai_auth_error()
        if auth is not None:
            return auth
        data = [{"id": m, "object": "model", "created": 1686935002,
                 "owned_by": "google"}
                for m in sorted(_MODELS)]
        return self.json_response({"object": "list", "data": data})


    def handle_unknown_route(self):
        return self._error(
            404,
            f"{request.path} is not a valid path.",
            "NOT_FOUND",
        )


def _split_words(text: str, n: int = 4) -> list[str]:
    words = text.split(" ")
    if len(words) <= n:
        return words if words else [text]
    size = max(1, len(words) // n)
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i:i + size]))
        i += size
    return [out[0]] + [" " + p for p in out[1:]]
