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


def setting_reference_contract():
    import ast
    from html.parser import HTMLParser
    root = LOCALES_DIR.parent
    tree = ast.parse((root / 'schemas.py').read_text(encoding='utf-8'))
    contract = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == 'MESSAGE_REFERENCES' for t in node.targets))
    contract.update({key: {'setting': 'START_OVER'} | contract.get(key, {})
                     for key in ('err_resume_old_database', 'err_resume_configuration_changed')})
    contract['notice_start_setting'] = {'setting': 'START_OVER'}
    class References(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            value = attrs.get('data-i18n-refs')
            key = attrs.get('data-i18n')
            if value is not None and key is not None:
                contract[key] = json.loads(value)
    for path in (root / 'templates').rglob('*.html'):
        References().feed(path.read_text(encoding='utf-8'))
    return contract


def assert_setting_placeholders(strings, contract):
    for key, references in contract.items():
        for name, field in references.items():
            assert field and '{' + name + '}' in strings[key], (key, name, field)


def test_setting_messages_carry_references_in_every_locale():
    contract = setting_reference_contract()
    assert len(contract) >= 15, 'the contract must cover hints as well as refusal messages'
    for language, strings in load_locales().items():
        assert_setting_placeholders(strings, contract)
        label = strings['lbl_start_over'].rstrip(':').strip()
        for key in ('err_resume_old_database', 'err_resume_configuration_changed'):
            assert label not in strings[key], (language, key)


def test_setting_reference_guard_detects_a_quoted_label():
    import pytest
    contract = setting_reference_contract()
    strings = dict(load_locales()['en'])
    assert_setting_placeholders(strings, contract)
    strings['hint_max_size'] = strings['hint_max_size'].replace('{resolution}', 'Max Resolution')
    with pytest.raises(AssertionError):
        assert_setting_placeholders(strings, contract)



def quoted_control_labels(strings):
    found = []
    for label_key, label in strings.items():
        if not label_key.startswith('lbl_'):
            continue
        variants = {label.rstrip(':').strip(), re.sub(r"\s*\([^)]*\)", '', label).rstrip(':').strip()}
        for key, message in strings.items():
            if not key.startswith(('hint_', 'warn_', 'err_', 'msg_')):
                continue
            for variant in variants:
                if len(variant) < 8:
                    continue
                if any((left + variant + right).casefold() in message.casefold()
                       for left, right in ((chr(34), chr(34)), (chr(39), chr(39)), ('«', '»'))):
                    found.append((key, label_key))
    return found


def test_explanations_do_not_quote_a_copied_control_label():
    for language, strings in load_locales().items():
        assert not quoted_control_labels(strings), (language, quoted_control_labels(strings))
    sample = {'lbl_resolution': 'Maximum Resolution (px):', 'hint_new': 'Lower "Maximum Resolution".'}
    assert quoted_control_labels(sample) == [('hint_new', 'lbl_resolution')]
    sample['hint_new'] = 'Lower {resolution}.'
    assert not quoted_control_labels(sample)
