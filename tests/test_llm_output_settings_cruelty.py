# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_loader import ConfigManager
from config_validator import Settings

from test_llm_output_settings import ai_off, ai_on, validate

INVALID = {"type": "i18n", "value": "err_output_columns_invalid"}


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    m = ConfigManager(tmp_path)
    monkeypatch.setattr(m, "get_env_tokens", lambda: {})
    for key in list(os.environ):
        if key.endswith("_TOKEN"):
            monkeypatch.delenv(key)
    (tmp_path / "input").mkdir()
    return m


@pytest.mark.parametrize("bad", [
    pytest.param('say"hi', id="double-quote"),
    pytest.param("back\\slash", id="backslash"),
    pytest.param('genre,a"b', id="quote-in-second-key"),
])
def test_output_columns_refuse_quote_and_backslash(mgr, tmp_path, bad):
    errors, _ = validate(mgr, ai_on(tmp_path, LLM_OUTPUT_COLUMNS=bad))
    assert errors.get("LLM_OUTPUT_COLUMNS") == INVALID, repr(bad)


@pytest.mark.parametrize("good", [
    pytest.param("имя,значение",
                 id="cyrillic"),
    pytest.param("\U0001f40d" * 64, id="sixty-four-emoji"),
    pytest.param("a;b", id="semicolon-is-one-key"),
    pytest.param("{a}", id="braces"),
    pytest.param("first name,last name", id="internal-spaces"),
    pytest.param("café", id="accented"),
])
def test_output_columns_accept_international_and_odd_but_honest_keys(
        mgr, tmp_path, good):
    errors, _ = validate(mgr, ai_on(tmp_path, LLM_OUTPUT_COLUMNS=good))
    assert "LLM_OUTPUT_COLUMNS" not in errors, (good, errors)


def _bounds(field_name):
    return sorted(repr(meta) for meta in Settings.model_fields[field_name].metadata)


def test_the_two_attempt_knobs_share_one_bounds_table():
    assert _bounds("LLM_JSON_MAX_ATTEMPTS") == _bounds("LLM_MAX_RETRIES")


@pytest.mark.parametrize("value", ["3", True, 3.0, "3.5", " 3 "])
def test_attempts_field_types_like_its_sibling(mgr, tmp_path, value):
    attempts_refused = "LLM_JSON_MAX_ATTEMPTS" in validate(
        mgr, ai_on(tmp_path, LLM_JSON_MAX_ATTEMPTS=value))[0]
    retries_refused = "LLM_MAX_RETRIES" in validate(
        mgr, ai_on(tmp_path, LLM_MAX_RETRIES=value))[0]
    assert attempts_refused == retries_refused, repr(value)


def test_an_older_settings_file_loads_with_answer_format_defaults(mgr, tmp_path):
    payload = ai_on(tmp_path)
    for key in ("LLM_OUTPUT_MODE", "LLM_OUTPUT_COLUMNS",
                "LLM_OUTPUT_COLUMN_INSTRUCTIONS", "LLM_JSON_MAX_ATTEMPTS",
                "LLM_ABORT_ON_MALFORMED_JSON"):
        assert key not in payload
    settings_obj, errors, _ = mgr.validate_draft(payload)
    assert errors == {}
    assert settings_obj is not None
    assert settings_obj.LLM_OUTPUT_MODE == "table_per_file"
    assert settings_obj.LLM_OUTPUT_COLUMNS == "answer"
    assert settings_obj.LLM_OUTPUT_COLUMN_INSTRUCTIONS == ""
    assert settings_obj.LLM_JSON_MAX_ATTEMPTS == 3
    assert settings_obj.LLM_ABORT_ON_MALFORMED_JSON is False

    mgr.save_settings(settings_obj)
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert on_disk["LLM_OUTPUT_COLUMNS"] == "answer"


def test_instructions_field_is_dormant_with_ai_off_and_typed_with_ai_on(mgr, tmp_path):
    errors, merged = validate(mgr, ai_off(tmp_path, LLM_OUTPUT_COLUMN_INSTRUCTIONS=123))
    assert errors == {}
    assert merged["LLM_OUTPUT_COLUMN_INSTRUCTIONS"] == 123

    errors, _ = validate(mgr, ai_on(tmp_path, LLM_OUTPUT_COLUMN_INSTRUCTIONS=123))
    assert "LLM_OUTPUT_COLUMN_INSTRUCTIONS" in errors


def test_column_error_keys_are_literal_in_src_and_present_in_every_locale():
    keys = ("err_output_columns_invalid", "err_output_columns_duplicate",
            "err_output_columns_reserved")
    validator_source = (SRC / "config_validator.py").read_text(encoding="utf-8")
    for key in keys:
        assert re.search(rf'"{key}"', validator_source), f"{key} is not a literal"

    locale_files = sorted((SRC / "locales").glob("*.json"))
    assert locale_files, "no locale files discovered"
    for locale_file in locale_files:
        strings = json.loads(locale_file.read_text(encoding="utf-8"))
        for key in keys:
            assert strings.get(key, "").strip(), (locale_file.name, key)
