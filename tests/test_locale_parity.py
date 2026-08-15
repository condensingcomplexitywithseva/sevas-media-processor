# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import re
from pathlib import Path


LOCALES_DIR = Path(__file__).resolve().parents[1] / "src" / "locales"

PLACEHOLDER = re.compile(r"\{\w+\}")


def load_locales():
    locales = {
        path.stem: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(LOCALES_DIR.glob("*.json"))
    }
    assert len(locales) >= 2, f"expected at least en+ru in {LOCALES_DIR}"
    return locales


def test_all_locales_define_the_same_keys():
    locales = load_locales()
    all_keys = set().union(*(d.keys() for d in locales.values()))
    problems = [
        f"{name}.json is missing: {sorted(all_keys - set(d))}"
        for name, d in locales.items()
        if all_keys - set(d)
    ]
    assert not problems, "\n".join(problems)


def test_every_string_has_the_same_line_breaks_in_every_locale():
    locales = load_locales()
    names = sorted(locales)
    reference = locales[names[0]]
    problems = []
    for key, ref_value in reference.items():
        expected = ref_value.count("\n")
        for name in names[1:]:
            value = locales[name].get(key)
            if value is not None and value.count("\n") != expected:
                problems.append(
                    f"{key}: {names[0]} has {expected} newline(s), "
                    f"{name} has {value.count(chr(10))}"
                )
    assert not problems, (
        "line-break structure differs between locales:\n  " + "\n  ".join(problems)
    )


def test_every_string_uses_the_same_placeholders_in_every_locale():
    locales = load_locales()
    names = sorted(locales)
    reference = locales[names[0]]
    problems = []
    for key, ref_value in reference.items():
        expected = set(PLACEHOLDER.findall(ref_value))
        for name in names[1:]:
            value = locales[name].get(key)
            if value is not None and set(PLACEHOLDER.findall(value)) != expected:
                problems.append(
                    f"{key}: {names[0]} uses {sorted(expected)}, "
                    f"{name} uses {sorted(set(PLACEHOLDER.findall(value)))}"
                )
    assert not problems, (
        "{placeholder} tokens differ between locales:\n  " + "\n  ".join(problems)
    )


def test_archive_locked_offers_three_ways_out_in_every_locale():
    problems = []
    for name, strings in load_locales().items():
        value = strings["err_archive_locked"]
        missing = [marker for marker in ("1.", "2.", "3.") if marker not in value]
        if missing:
            problems.append(f"{name}.json is missing route(s) {missing}")
        if "current_run" not in value:
            problems.append(f"{name}.json never names the current_run folder")
    assert not problems, "\n".join(problems)
