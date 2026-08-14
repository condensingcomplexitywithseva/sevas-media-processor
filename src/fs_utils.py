# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from pathlib import Path
import math
import os
import re


def get_safe_path(path_obj: Path) -> str:
    safe_path = str(path_obj.resolve())
    if os.name == "nt":
        if safe_path.startswith("\\\\?\\"):
            return safe_path
        if safe_path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + safe_path[2:]
        return "\\\\?\\" + safe_path
    return safe_path


_LONG_PATH_PREFIX = "\\\\?\\"
_LONG_PATH_UNC_PREFIX = "\\\\?\\UNC\\"
_UNC_ROOT = "\\\\"


def _as_repr_escaped(text: str) -> str:
    return text.replace("\\", "\\\\")


_QUOTED_RUN = re.compile(r"(['\"])((?:(?!\1).)*)\1", re.DOTALL)


def _looks_repr_escaped(text: str) -> bool:
    return all(len(run) % 2 == 0 for run in re.findall(r"\\+", text))


def _undouble_backslashes_inside_quotes(text: str) -> str:
    def undouble(match: "re.Match") -> str:
        quote, body = match.group(1), match.group(2)
        if "\\\\" not in body or not _looks_repr_escaped(body):
            return match.group(0)
        return quote + body.replace("\\\\", "\\") + quote

    return _QUOTED_RUN.sub(undouble, text)


def humanize_paths(text: str) -> str:
    text = _undouble_backslashes_inside_quotes(text)
    for prefix, replacement in (
        (_as_repr_escaped(_LONG_PATH_UNC_PREFIX), _as_repr_escaped(_UNC_ROOT)),
        (_as_repr_escaped(_LONG_PATH_PREFIX), ""),
        (_LONG_PATH_UNC_PREFIX, _UNC_ROOT),
        (_LONG_PATH_PREFIX, ""),
    ):
        text = text.replace(prefix, replacement)
    return text


def format_hms(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(f"format_hms expects a finite non-negative time, got {seconds!r}")
    total_hundredths = round(seconds * 100)
    hours = total_hundredths // 360000
    minutes = (total_hundredths // 6000) % 60
    secs = (total_hundredths // 100) % 60
    hundredths = total_hundredths % 100
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{hundredths:02d}"


def format_hms_for_filename(seconds: float) -> str:
    return format_hms(seconds).replace(":", "_").replace(".", "_")


_WINDOWS_ILLEGAL_CHARS = frozenset('<>:"/\\|?*')


def sanitize_filename_prefix(stem: str, max_length: int) -> str:
    if max_length < 0:
        raise ValueError(f"sanitize_filename_prefix expects a non-negative length, got {max_length!r}")
    replaced = "".join(
        " "
        if ch in _WINDOWS_ILLEGAL_CHARS
        or ord(ch) < 0x20
        or ord(ch) == 0x7F
        or 0xD800 <= ord(ch) <= 0xDFFF
        else ch
        for ch in stem
    )
    collapsed = " ".join(replaced.split())
    return collapsed[:max_length].rstrip(". ")


_TEXT_FORBIDDEN = ({chr(c) for c in range(0x20)} - {"\t", "\n", "\r"}) | {"\x7f"}


def text_looks_binary(text: str) -> bool:
    return any(ch in _TEXT_FORBIDDEN for ch in text)


def read_prompt(value: str, mode: str) -> str:
    if mode == "FILE" and value.strip():
        with open(get_safe_path(Path(value)), "r", encoding="utf-8") as f:
            content = f.read()
        if text_looks_binary(content):
            raise ValueError(f"prompt file is not text (control characters): {value}")
        return content
    return value
