# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import html
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



TEMPLATES = SRC / "templates"
INLINE_TEXT = re.compile(r'data-i18n="([\w.]+)"[^>]*>([^<]*)<')
PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}|\{\w+\}")
PAGE_LANG = re.compile(r'<html\b[^>]*\blang="(\w+)"')


def authoring_locale():
    match = PAGE_LANG.search((TEMPLATES / "base.html").read_text(encoding="utf-8"))
    assert match, "base.html declares no <html lang=...>"
    return match.group(1)


def normalise(text):
    text = html.unescape(text)
    text = PLACEHOLDER.sub("{}", text)
    return re.sub(r"\s+", " ", text).strip()


def inline_defaults():
    for path in sorted(TEMPLATES.rglob("*.html")):
        text = path.read_text(encoding="utf-8")
        for match in INLINE_TEXT.finditer(text):
            line = text[: match.start()].count("\n") + 1
            yield f"{path.name}:{line}", match.group(1), match.group(2)


def test_the_template_sweep_actually_finds_the_elements():
    assert len(list(inline_defaults())) > 200


def test_normalise_positive_controls():
    assert normalise("Must be &gt;= 1.\n  next") == "Must be >= 1. next"
    assert normalise("for {{ provider_key }} now") == normalise("for {provider} now")
    assert normalise("a &amp; b") == "a & b"
    assert normalise("Media Files") != normalise("Media Types")


def test_every_inline_template_default_matches_the_authoring_locale():
    strings = load_locales()[authoring_locale()]
    problems = []
    for where, key, inline in inline_defaults():
        value = strings.get(key)
        if value is None:
            problems.append(f"{where}: {key} has no string in the authoring locale")
            continue
        if normalise(inline) != normalise(value):
            problems.append(
                f"{where}: {key}\n"
                f"      template: {normalise(inline)[:100]!r}\n"
                f"      locale:   {normalise(value)[:100]!r}"
            )
    assert not problems, (
        "inline data-i18n text drifted from the locale (it is dead once the "
        "key loads and the fallback otherwise - make it the locale's string):"
        "\n  " + "\n  ".join(problems)
    )


LITERAL_TRANSLATION = re.compile(r"""\bgetT\s*\(\s*(['"])([\w.-]+)\1\s*(?=[,)])""")


def literal_translation_keys(source):
    return {match.group(2) for match in LITERAL_TRANSLATION.finditer(source)}


def test_every_literal_translation_lookup_exists_in_every_locale():
    keys = set()
    for path in sorted(SRC.rglob('*.js')) + sorted(SRC.rglob('*.html')):
        keys.update(literal_translation_keys(path.read_text(encoding='utf-8')))
    assert len(keys) > 30
    for language, strings in load_locales().items():
        assert keys <= strings.keys(), (language, sorted(keys - strings.keys()))


def test_literal_translation_scan_detects_missing_keys_with_and_without_fallbacks():
    keys = literal_translation_keys("""window.getT('missing_one');
        getT("missing_two", 'fallback'); getT('tab_' + tab); getT(variable);""")
    assert keys == {'missing_one', 'missing_two'}
    assert keys - {'missing_one'} == {'missing_two'}


def test_export_error_category_translations_cover_the_backend():
    import ast
    tree = ast.parse((SRC / 'data_exporter.py').read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'error_category')
    categories = {node.value.value for node in ast.walk(function)
                  if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
                  and isinstance(node.value.value, str)}
    assert {'locked', 'disk_full', 'access_denied'} <= categories
    for language, strings in load_locales().items():
        assert all(strings.get('export_error_' + category) for category in categories), language
