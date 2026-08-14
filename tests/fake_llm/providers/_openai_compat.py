# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import random
import string
import time
from typing import Any, Optional

from ..content import estimate_tokens

_DEFAULT_CREATED = 1751700000


def rand_suffix(n: int = 24, alphabet: str = string.ascii_letters + string.digits) -> str:
    return "".join(random.choice(alphabet) for _ in range(n))



def oai_error(message: str, *, type: str = "invalid_request_error",
              param: Optional[str] = None, code: Optional[str] = None,
              drop_param: bool = False) -> dict:
    err: dict[str, Any] = {"message": message, "type": type}
    if not drop_param:
        err["param"] = param
    err["code"] = code
    return {"error": err}



def chat_completion_body(
    model: str,
    text: str,
    *,
    prompt_text: str = "",
    id: Optional[str] = None,
    id_prefix: str = "chatcmpl-",
    created: Optional[int] = None,
    finish_reason: str = "stop",
    reasoning_tokens: int = 0,
    system_fingerprint: Optional[str] = None,
    include_usage_details: bool = True,
    message_extra: Optional[dict] = None,
    usage_extra: Optional[dict] = None,
    refusal: Any = None,
) -> dict:
    prompt_tokens = estimate_tokens(prompt_text) or 16
    completion_tokens = estimate_tokens(text) + reasoning_tokens

    message: dict[str, Any] = {"role": "assistant", "content": text}
    if refusal is not None or refusal is None:
        message["refusal"] = refusal
    if message_extra:
        message.update(message_extra)

    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if include_usage_details:
        usage["prompt_tokens_details"] = {"cached_tokens": 0, "audio_tokens": 0}
        usage["completion_tokens_details"] = {
            "reasoning_tokens": reasoning_tokens,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        }
    if usage_extra:
        usage.update(usage_extra)

    body: dict[str, Any] = {
        "id": id or (id_prefix + rand_suffix()),
        "object": "chat.completion",
        "created": created if created is not None else _DEFAULT_CREATED,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }
    if system_fingerprint is not None:
        body["system_fingerprint"] = system_fingerprint
    return body



def _chunks(text: str, n: int = 4) -> list[str]:
    words = text.split(" ")
    if len(words) <= n:
        return words
    size = max(1, len(words) // n)
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i:i + size]))
        i += size
    return [out[0]] + [" " + p for p in out[1:]]


def chat_completion_sse_events(
    model: str,
    text: str,
    *,
    id: Optional[str] = None,
    id_prefix: str = "chatcmpl-",
    created: Optional[int] = None,
    system_fingerprint: Optional[str] = None,
    finish_reason: str = "stop",
    include_usage: bool = False,
    prompt_text: str = "",
    reasoning_tokens: int = 0,
    done: bool = True,
    delta_extra_first: Optional[dict] = None,
) -> list[str]:
    import json as _json

    cid = id or (id_prefix + rand_suffix())
    cts = created if created is not None else _DEFAULT_CREATED

    def frame(delta: dict, fin=None, choices=None, usage=None) -> str:
        obj: dict[str, Any] = {
            "id": cid, "object": "chat.completion.chunk", "created": cts,
            "model": model,
        }
        if system_fingerprint is not None:
            obj["system_fingerprint"] = system_fingerprint
        if choices is None:
            obj["choices"] = [{"index": 0, "delta": delta,
                               "logprobs": None, "finish_reason": fin}]
        else:
            obj["choices"] = choices
        if include_usage:
            obj["usage"] = usage
        return "data: " + _json.dumps(obj, ensure_ascii=False)

    events: list[str] = []
    first_delta: dict[str, Any] = {"role": "assistant", "content": ""}
    if delta_extra_first:
        first_delta.update(delta_extra_first)
    events.append(frame(first_delta))
    for piece in _chunks(text):
        events.append(frame({"content": piece}))
    events.append(frame({}, fin=finish_reason))

    if include_usage:
        prompt_tokens = estimate_tokens(prompt_text) or 16
        completion_tokens = estimate_tokens(text) + reasoning_tokens
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        events.append(frame({}, choices=[], usage=usage))

    if done:
        events.append("data: [DONE]")
    return events



def flatten_prompt_text(messages: Any) -> str:
    out: list[str] = []
    if not isinstance(messages, list):
        return ""
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
