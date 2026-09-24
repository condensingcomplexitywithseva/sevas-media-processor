# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import create_model

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import app_context
import llm_client
from config_validator import (
    IDENTITY_PROVIDER_FIELDS, IDENTITY_SETTINGS, NON_IDENTITY_PROVIDER_FIELDS, NON_IDENTITY_SETTINGS,
    ProviderConfig, Settings, classification_errors,
)

IDENTITY_URL = "http://127.0.0.1:9/v1/chat/completions"
IDENTITY_TOKEN = "identity-token-a"
PROMPT_FILE_TEXT = "Describe the receipt"


def _identity(tmp_path, **overrides):
    for name in ("input", "input2", "output", "output2"):
        (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / "prompt.txt").write_text(PROMPT_FILE_TEXT, encoding="utf-8")
    values: dict[str, Any] = {
        "INPUT_FOLDER_PATH": "{tmp}/input", "OUTPUT_FOLDER_PATH": "{tmp}/output",
        "ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai", "LLM_PROVIDERS": {"openai": {"url": IDENTITY_URL}},
    }
    values.update(overrides)
    values = {name: value.replace("{tmp}", str(tmp_path)) if isinstance(value, str) else value
              for name, value in values.items()}
    settings = Settings(**values)
    llm = llm_client.LLMClient(settings, token=IDENTITY_TOKEN) if settings.ENABLE_LLM_INFERENCE else None
    return app_context.processing_identity(settings, llm)


SETTING_CASES: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {
    "GUI_LANGUAGE": [({}, {"GUI_LANGUAGE": "ru"})],
    "INPUT_FOLDER_PATH": [({}, {"INPUT_FOLDER_PATH": "{tmp}/input2"})],
    "OUTPUT_FOLDER_PATH": [({}, {"OUTPUT_FOLDER_PATH": "{tmp}/output2"})],
    "LOGGING_LEVEL": [({}, {"LOGGING_LEVEL": "DEBUG"})],
    "START_OVER": [({}, {"START_OVER": False})],
    "NO_RETRY_STATUSES": [({}, {"NO_RETRY_STATUSES": ["ok", "fail"]})],
    "ENABLE_LLM_INFERENCE": [({}, {"ENABLE_LLM_INFERENCE": False})],
    "LLM_PROVIDER": [({}, {"LLM_PROVIDER": "claude"})],
    "LLM_PROVIDERS": [({}, {"LLM_PROVIDERS": {"openai": {"url": IDENTITY_URL, "model": "another-model"}}})],
    "LLM_USER_PROMPT": [({}, {"LLM_USER_PROMPT": "another task"})],
    "LLM_USER_PROMPT_MODE": [({"LLM_USER_PROMPT": "{tmp}/prompt.txt"}, {"LLM_USER_PROMPT_MODE": "FILE"})],
    "LLM_SYSTEM_PROMPT": [({}, {"LLM_SYSTEM_PROMPT": "another role"})],
    "LLM_SYSTEM_PROMPT_MODE": [({"LLM_SYSTEM_PROMPT": "{tmp}/prompt.txt"}, {"LLM_SYSTEM_PROMPT_MODE": "FILE"})],
    "LLM_OUTPUT_MODE": [({}, {"LLM_OUTPUT_MODE": "table_per_page"})],
    "LLM_OUTPUT_COLUMNS": [({}, {"LLM_OUTPUT_COLUMNS": "answer,colour"})],
    "LLM_OUTPUT_COLUMN_INSTRUCTIONS": [({}, {"LLM_OUTPUT_COLUMN_INSTRUCTIONS": "answer: the colour"})],
    "LLM_JSON_MAX_ATTEMPTS": [({}, {"LLM_JSON_MAX_ATTEMPTS": 6})],
    "LLM_ABORT_ON_MALFORMED_JSON": [({}, {"LLM_ABORT_ON_MALFORMED_JSON": True})],
    "MAX_JPEGS_PER_INFERENCE": [({}, {"MAX_JPEGS_PER_INFERENCE": 5})],
    "MAX_CONSECUTIVE_LLM_FAILURES": [({}, {"MAX_CONSECUTIVE_LLM_FAILURES": 2})],
    "HALT_ON_LLM_PARSE_ERROR": [({}, {"HALT_ON_LLM_PARSE_ERROR": False})],
    "LLM_MAX_RETRIES": [({}, {"LLM_MAX_RETRIES": 9})],
    "LLM_TIMEOUT_SECONDS": [({}, {"LLM_TIMEOUT_SECONDS": 7})],
    "LLM_RETRY_SLEEP_SECONDS": [({}, {"LLM_RETRY_SLEEP_SECONDS": 11})],
    "ENV_TOKENS": [({}, {"ENV_TOKENS": {"OPENAI_TOKEN": "identity-token-b"}})],
    "MAX_DIMENSION": [({}, {"MAX_DIMENSION": 1000})],
    "JPEG_QUALITY": [({}, {"JPEG_QUALITY": 80})],
    "PDF_SCALE": [({}, {"PDF_SCALE": 3})],
    "MAX_FILE_SIZE_KB": [({}, {"MAX_FILE_SIZE_KB": 500})],
    "LOWEST_QUALITY": [({}, {"LOWEST_QUALITY": 30})],
    "PILLOW_MAX_PIXELS": [({}, {"PILLOW_MAX_PIXELS": 1000}), ({}, {"PILLOW_MAX_PIXELS": 10**9})],
    "WHITE_BACKGROUND": [({}, {"WHITE_BACKGROUND": (0, 0, 0)})],
    "OUTPUT_FILENAME_PREFIX_LENGTH": [({}, {"OUTPUT_FILENAME_PREFIX_LENGTH": 0})],
    "OUTPUT_FILENAME_TIMESTAMPS": [({}, {"OUTPUT_FILENAME_TIMESTAMPS": False})],
    "VIDEO_MODE": [({}, {"VIDEO_MODE": "SAMPLING"})],
    "VIDEO_RANGE": [({}, {"VIDEO_RANGE": "00:00:01-00:00:05"})],
    "VIDEO_SUMMARY_TARGET_TOTAL_FRAMES": [({}, {"VIDEO_SUMMARY_TARGET_TOTAL_FRAMES": 12})],
    "VIDEO_SUMMARY_SCENE_SENSITIVITY": [({}, {"VIDEO_SUMMARY_SCENE_SENSITIVITY": 2.0})],
    "VIDEO_SAMPLING_CAPTURE_RATE_FPS": [({"VIDEO_MODE": "SAMPLING"}, {"VIDEO_SAMPLING_CAPTURE_RATE_FPS": 3.0})],
    "VIDEO_SAMPLING_MAX_FRAMES_BUDGET": [({"VIDEO_MODE": "SAMPLING"}, {"VIDEO_SAMPLING_MAX_FRAMES_BUDGET": 7})],
    "VIDEO_SAMPLING_SCENE_SENSITIVITY": [({"VIDEO_MODE": "SAMPLING"}, {"VIDEO_SAMPLING_SCENE_SENSITIVITY": 1.5})],
    "ANIMATION_RANGE": [({}, {"ANIMATION_RANGE": "1-3"})],
    "ANIMATION_TARGET_TOTAL_FRAMES": [({}, {"ANIMATION_TARGET_TOTAL_FRAMES": 3})],
    "ANIMATION_SCENE_SENSITIVITY": [({}, {"ANIMATION_SCENE_SENSITIVITY": 1.0})],
    "IMAGE_RANGE": [({}, {"IMAGE_RANGE": "1-2"})],
    "DOCUMENT_RANGE": [({}, {"DOCUMENT_RANGE": "2-3"})],
    "DOCUMENT_MAX_PAGES": [({}, {"DOCUMENT_MAX_PAGES": 5})],
}

PROVIDER_CASES: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {
    "url": [({}, {"url": IDENTITY_URL.replace(":9/", ":10/")})],
    "model": [({}, {"model": "another-model"})],
    "auth_header_key": [({}, {"auth_header_key": "X-Api-Key"})],
    "auth_header_format": [({}, {"auth_header_format": "Token {token}"})],
    "extra_header_key": [({"extra_header_key": "API-Version", "extra_header_value": "2"},
                          {"extra_header_key": "Other-Header"})],
    "extra_header_value": [({"extra_header_key": "API-Version", "extra_header_value": "2"},
                            {"extra_header_value": "3"})],
    "require_max_tokens": [({}, {"require_max_tokens": True}),
                           ({"require_max_tokens": True}, {"require_max_tokens": False})],
    "max_tokens": [({"require_max_tokens": True, "max_tokens": 1000}, {"max_tokens": 4000}),
                   ({"max_tokens": 1000}, {"max_tokens": 4000})],
    "max_tokens_field": [({"require_max_tokens": True, "max_tokens_field": "max_tokens"},
                          {"max_tokens_field": "max_completion_tokens"})],
    "system_prompt_location": [({}, {"system_prompt_location": "top_level"})],
    "image_payload_style": [({}, {"image_payload_style": "base64_dict"})],
    "reasoning_handling": [({}, {"reasoning_handling": "strip_xml"})],
    "response_extraction_path": [({}, {"response_extraction_path": "choices[0].message.reasoning"})],
}


def _provider_identity(tmp_path, fields):
    return _identity(tmp_path, LLM_PROVIDERS={"openai": {"url": IDENTITY_URL, **fields}})


def test_every_setting_and_provider_field_is_classified_exactly_once():
    assert classification_errors(Settings.model_fields, IDENTITY_SETTINGS, NON_IDENTITY_SETTINGS) == []
    assert classification_errors(ProviderConfig.model_fields, IDENTITY_PROVIDER_FIELDS,
                                 NON_IDENTITY_PROVIDER_FIELDS) == []


def test_the_classification_guard_names_unclassified_duplicated_and_stale_fields():
    extended = create_model("ExtendedSettings", __base__=Settings, NEW_SETTING=(int, 1))
    assert classification_errors(extended.model_fields, IDENTITY_SETTINGS, NON_IDENTITY_SETTINGS) == [
        "NEW_SETTING: unclassified"]
    extended_provider = create_model("ExtendedProvider", __base__=ProviderConfig, temperature=(float, 0.0))
    assert classification_errors(extended_provider.model_fields, IDENTITY_PROVIDER_FIELDS,
                                 NON_IDENTITY_PROVIDER_FIELDS) == ["temperature: unclassified"]
    assert classification_errors(Settings.model_fields, IDENTITY_SETTINGS | {"JPEG_QUALITY", "OLD_SETTING"},
                                 NON_IDENTITY_SETTINGS | {"JPEG_QUALITY"}) == [
        "JPEG_QUALITY: on both sides", "OLD_SETTING: not a declared field"]


def test_every_field_has_an_effect_case_that_edits_it():
    assert set(SETTING_CASES) == set(Settings.model_fields)
    assert set(PROVIDER_CASES) == set(ProviderConfig.model_fields)
    for cases in (SETTING_CASES, PROVIDER_CASES):
        for field, field_cases in cases.items():
            assert field_cases and all(field in edit for _, edit in field_cases), field


@pytest.mark.parametrize("field", sorted(Settings.model_fields))
def test_a_setting_changes_the_run_identity_exactly_when_classified_as_identity(tmp_path, field):
    for base, edit in SETTING_CASES[field]:
        changed = _identity(tmp_path, **{**base, **edit}) != _identity(tmp_path, **base)
        assert changed == (field in IDENTITY_SETTINGS), (base, edit)


@pytest.mark.parametrize("field", sorted(ProviderConfig.model_fields))
def test_a_provider_field_changes_the_run_identity_exactly_when_classified_as_identity(tmp_path, field):
    for base, edit in PROVIDER_CASES[field]:
        changed = _provider_identity(tmp_path, {**base, **edit}) != _provider_identity(tmp_path, base)
        assert changed == (field in IDENTITY_PROVIDER_FIELDS), (base, edit)


def test_an_unclassified_field_counts_toward_the_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(app_context, "NON_IDENTITY_SETTINGS", NON_IDENTITY_SETTINGS - {"PILLOW_MAX_PIXELS"})
    assert _identity(tmp_path, PILLOW_MAX_PIXELS=1000) != _identity(tmp_path)
    monkeypatch.setattr(llm_client, "NON_IDENTITY_PROVIDER_FIELDS", NON_IDENTITY_PROVIDER_FIELDS - {"max_tokens"})
    assert _provider_identity(tmp_path, {"max_tokens": 4000}) != _provider_identity(tmp_path, {"max_tokens": 1000})
