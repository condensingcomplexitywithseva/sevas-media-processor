# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import math

DEFAULT_ANSWER = "The quick brown fox jumps over the lazy dog."

DEFAULT_THINKING = (
    "Let me look at the image carefully. I can make out printed text. "
    "I will transcribe exactly what is visible, preserving line breaks, "
    "and avoid guessing at anything obscured."
)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


class ContentPolicy:

    def __init__(self, answer: str | None = None, thinking: str | None = None):
        self._answer = answer
        self._thinking = thinking

    def answer(self, body: dict | None = None) -> str:
        if self._answer is not None:
            return self._answer
        if isinstance(body, dict) and isinstance(body.get("fake_answer"), str):
            return body["fake_answer"]
        return DEFAULT_ANSWER

    def thinking(self, body: dict | None = None) -> str:
        if self._thinking is not None:
            return self._thinking
        if isinstance(body, dict) and isinstance(body.get("fake_thinking"), str):
            return body["fake_thinking"]
        return DEFAULT_THINKING
