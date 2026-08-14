# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
LOCALES_DIR = SRC / "locales"

GET_T_CALL = re.compile(
    r"""getT\(\s*['"]([\w.]+)['"]\s*,\s*(['"])((?:\\.|(?!\2).)*)\2""",
    re.DOTALL,
)

SEPARATORS = (":", "\\n", " ", "-")


def trailing_separator(text):
    for separator in SEPARATORS:
        if text.endswith(separator):
            return separator
    return None


def load_locales():
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(LOCALES_DIR.glob("*.json"))
    }


def find_calls():
    for source in sorted(SRC.rglob("*.js")) + sorted(SRC.rglob("*.html")):
        text = source.read_text(encoding="utf-8")
        for match in GET_T_CALL.finditer(text):
            key, _, default = match.groups()
            line = text[: match.start()].count("\n") + 1
            yield f"{source.name}:{line}", key, default


def test_the_sweep_actually_finds_the_calls():
    assert len(list(find_calls())) > 20


@pytest.mark.parametrize("locale_name", sorted(p.stem for p in LOCALES_DIR.glob("*.json")))
def test_no_default_promises_a_separator_the_translation_lacks(locale_name):
    strings = load_locales()[locale_name]
    problems = []
    for where, key, default in find_calls():
        promised = trailing_separator(default)
        if not promised:
            continue
        value = strings.get(key)
        if value is None:
            continue
        if not trailing_separator(value.replace("\n", "\\n")):
            problems.append(
                f"{where}: getT('{key}', ...ending {promised!r}) but "
                f"{locale_name}.json has {value!r} - the strings will fuse. "
                f"Put the separator in the code, outside getT()."
            )
    assert not problems, "\n  " + "\n  ".join(problems)
