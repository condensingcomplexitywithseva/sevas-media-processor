# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from config_loader import ConfigManager, TokenManager
from schemas import ConfigurationError
from config_validator import Settings
from fake_llm.harness import READY_ENTRY, ready_ai

LEFT_OVER = "left-over-provider"



def validate(mgr, payload):
    if hasattr(mgr, "validate_draft"):
        _, errors, merged = mgr.validate_draft(payload)
        return errors, merged
    _, errors, merged = mgr.validate_and_cache(payload, update_cache=False)
    return errors, merged


def load_ui(mgr):
    if hasattr(mgr, "load_for_ui"):
        merged, errors = mgr.load_for_ui()
        return errors, merged
    _, errors, merged = mgr.load_permissive()
    return errors, merged



@pytest.fixture
def mgr(tmp_path, monkeypatch):
    m = ConfigManager(tmp_path)
    m.app_data_dir = tmp_path
    m.env_path = tmp_path / ".env"
    m.token_manager = TokenManager(m.env_path)
    monkeypatch.setattr(m, "get_env_tokens", lambda: {})
    for key in list(os.environ):
        if key.endswith("_TOKEN"):
            monkeypatch.delenv(key)
    (tmp_path / "input").mkdir()
    return m


def base_paths(tmp_path, **overrides) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "INPUT_FOLDER_PATH": str(tmp_path / "input"),
        "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
    }
    payload.update(overrides)
    return payload



def test_valid_payload_has_no_errors(mgr, tmp_path):
    errors, merged = validate(mgr, base_paths(tmp_path))
    assert errors == {}
    assert merged["INPUT_FOLDER_PATH"] == str(tmp_path / "input")
    assert merged["JPEG_QUALITY"] == 90


def test_out_of_bounds_and_bad_range_flagged(mgr, tmp_path):
    errors, merged = validate(mgr, base_paths(
        tmp_path, JPEG_QUALITY=500, IMAGE_RANGE="abc"))
    assert errors["JPEG_QUALITY"] == {"type": "max_bound", "value": "100"}
    assert errors["IMAGE_RANGE"] == {"type": "i18n", "value": "err_invalid_range"}
    assert merged["JPEG_QUALITY"] == 500
    assert merged["IMAGE_RANGE"] == "abc"


def test_quality_floor_above_start_flagged(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(tmp_path, JPEG_QUALITY=50, LOWEST_QUALITY=80))
    assert errors["LOWEST_QUALITY"]["value"] == "err_quality_floor_above_start"


def test_output_filename_prefix_length_bounds(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(tmp_path, OUTPUT_FILENAME_PREFIX_LENGTH=65))
    assert errors["OUTPUT_FILENAME_PREFIX_LENGTH"] == {"type": "max_bound", "value": "64"}
    errors, _ = validate(mgr, base_paths(tmp_path, OUTPUT_FILENAME_PREFIX_LENGTH=-1))
    assert "OUTPUT_FILENAME_PREFIX_LENGTH" in errors
    errors, _ = validate(mgr, base_paths(tmp_path, OUTPUT_FILENAME_PREFIX_LENGTH=0))
    assert "OUTPUT_FILENAME_PREFIX_LENGTH" not in errors


def test_pydantic_and_business_errors_reported_together(mgr, tmp_path):
    errors, _ = validate(mgr, {
        "INPUT_FOLDER_PATH": str(tmp_path / "does_not_exist"),
        "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
        "JPEG_QUALITY": 500,
    })
    assert "JPEG_QUALITY" in errors
    assert "INPUT_FOLDER_PATH" in errors
    assert errors["INPUT_FOLDER_PATH"]["value"] == "err_input_missing"


def test_path_overlap_flagged(mgr, tmp_path):
    errors, _ = validate(mgr, {
        "INPUT_FOLDER_PATH": str(tmp_path / "input"),
        "OUTPUT_FOLDER_PATH": str(tmp_path / "input" / "sub"),
    })
    assert errors["OUTPUT_FOLDER_PATH"]["value"] == "err_path_overlap"
    assert errors["INPUT_FOLDER_PATH"]["value"] == "err_path_overlap"


def test_llm_incomplete_ai_never_blames_mode_field(mgr, tmp_path):
    cases = [
        ({"LLM_PROVIDER": "custom"}, "LLM_PROVIDERS.custom.url"),
        ({"LLM_PROVIDER": "custom"}, "LLM_PROVIDERS.custom.model"),
        ({**ready_ai(), "LLM_USER_PROMPT": " "}, "LLM_USER_PROMPT"),
        ({**ready_ai(), "LLM_SYSTEM_PROMPT": " "}, "LLM_SYSTEM_PROMPT"),
    ]
    for overrides, expected_key in cases:
        errors, _ = validate(mgr, base_paths(
            tmp_path, **{"ENABLE_LLM_INFERENCE": True, **overrides}))
        assert expected_key in errors, (overrides, errors)
        assert "ENABLE_LLM_INFERENCE" not in errors, (overrides, errors)


def test_a_complete_provider_without_a_token_is_clean(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(tmp_path, **ready_ai()))
    assert errors == {}


PADDED_PROVIDER = {
    "url": " http://localhost:1234/v1/chat/completions ", "model": "qwen/qwen3.5-2b\t",
    "auth_header_key": "Authorization ", "auth_header_format": " Bearer {token}",
    "extra_header_key": " x-key", "extra_header_value": "value ",
    "response_extraction_path": "choices[0].message.content ",
}


def test_a_run_uses_single_line_settings_without_surrounding_spaces(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="custom",
        LLM_PROVIDERS={"custom": PADDED_PROVIDER},
        INPUT_FOLDER_PATH=f" {tmp_path / 'input'} ",
        OUTPUT_FOLDER_PATH=f"{tmp_path / 'output'} ")), encoding="utf-8")
    settings = mgr.load_strict()
    active = settings.ACTIVE_PROVIDER_CONFIG
    assert {field: getattr(active, field) for field in PADDED_PROVIDER} == \
        {field: value.strip() for field, value in PADDED_PROVIDER.items()}
    folders = (settings.INPUT_FOLDER_PATH, settings.OUTPUT_FOLDER_PATH)
    assert folders == (tmp_path / "input", tmp_path / "output")


def test_inner_spaces_and_the_max_tokens_field_rule_are_unchanged(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(tmp_path, **ready_ai(max_tokens_field="max tokens")))
    assert errors["LLM_PROVIDERS.custom.max_tokens_field"]["value"] == "err_max_tokens_field_invalid"
    from config_validator import ProviderConfig
    assert ProviderConfig(url="u", model=" my model ").model == "my model"
    folder = Settings.model_validate({"INPUT_FOLDER_PATH": " my media "}).INPUT_FOLDER_PATH
    assert str(folder) == "my media"


def test_paths_pasted_with_copy_as_path_validate_as_the_paths(mgr, tmp_path):
    prompt = tmp_path / "my prompt.txt"
    prompt.write_text("Read the total.", encoding="utf-8")
    errors, _ = validate(mgr, base_paths(
        tmp_path, INPUT_FOLDER_PATH=f'"{tmp_path / "input"}"',
        OUTPUT_FOLDER_PATH=f'"{tmp_path / "output"}"', **ready_ai(),
        LLM_USER_PROMPT_MODE="FILE", LLM_USER_PROMPT=f' "{prompt}" '))
    assert errors == {}
    folder = Settings.model_validate({"OUTPUT_FOLDER_PATH": f'"{tmp_path / "output"}"'}).OUTPUT_FOLDER_PATH
    assert str(folder) == str(tmp_path / "output")


def test_llm_mode_type_error_still_surfaces(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE="banana", LLM_PROVIDER="custom"))
    assert "ENABLE_LLM_INFERENCE" in errors


def test_llm_prompt_file_missing_flagged(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path,
        **ready_ai(),
        LLM_USER_PROMPT_MODE="FILE",
        LLM_USER_PROMPT=str(tmp_path / "missing.txt"),
    ))
    assert errors.get("LLM_USER_PROMPT") == {"type": "i18n", "value": "err_user_file_missing"}


@pytest.mark.parametrize("payload_bytes", [
    "привет".encode("utf-16"),
    "plain ascii".encode("utf-16-le"),
])
def test_llm_prompt_file_that_is_not_text_flagged_as_unreadable(mgr, tmp_path, payload_bytes):
    bad = tmp_path / "prompt.txt"
    bad.write_bytes(payload_bytes)
    errors, _ = validate(mgr, base_paths(
        tmp_path,
        **ready_ai(),
        LLM_USER_PROMPT_MODE="FILE",
        LLM_USER_PROMPT=str(bad),
    ))
    assert errors.get("LLM_USER_PROMPT") == {"type": "i18n", "value": "err_user_file_unreadable"}


def test_prompt_file_check_uses_the_long_path_spelling(mgr, tmp_path, monkeypatch):
    import config_validator
    from fs_utils import get_safe_path as real_get_safe_path

    recorded = []

    def recording(path_obj):
        recorded.append(Path(path_obj))
        return real_get_safe_path(path_obj)

    monkeypatch.setattr(config_validator, "get_safe_path", recording)

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("say hi", encoding="utf-8")
    errors, _ = validate(mgr, base_paths(
        tmp_path,
        **ready_ai(),
        LLM_USER_PROMPT_MODE="FILE",
        LLM_USER_PROMPT=str(prompt),
    ))
    assert not any(k.startswith("LLM_USER_PROMPT") for k in errors), errors
    assert prompt in recorded


def test_llm_disabled_skips_ai_checks(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=False, LLM_PROVIDER="custom"))
    assert errors == {}


def test_update_tokens_empty_string_deletes(tmp_path, monkeypatch):
    from config_loader import TokenManager
    monkeypatch.delenv("CUSTOM_TOKEN", raising=False)
    tm = TokenManager(tmp_path / ".env")
    tm.update_tokens({"custom": "sk-x"})
    assert tm.get_tokens() == {"CUSTOM_TOKEN": "sk-x"}
    assert os.environ.get("CUSTOM_TOKEN") == "sk-x"
    tm.update_tokens({"custom": ""})
    assert tm.get_tokens() == {}
    assert "CUSTOM_TOKEN" not in os.environ



def test_load_strict_ok_roundtrip(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(tmp_path)), encoding="utf-8")
    settings = mgr.load_strict()
    assert isinstance(settings, Settings)
    assert str(settings.INPUT_FOLDER_PATH) == str(tmp_path / "input")


def test_load_strict_raises_on_invalid_value(mgr, tmp_path):
    mgr.settings_path.write_text(
        json.dumps(base_paths(tmp_path, JPEG_QUALITY=500)), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        mgr.load_strict()


def test_load_strict_raises_on_business_error(mgr, tmp_path):
    mgr.settings_path.write_text(
        json.dumps({"INPUT_FOLDER_PATH": str(tmp_path / "nope"),
                    "OUTPUT_FOLDER_PATH": str(tmp_path / "out")}), encoding="utf-8")
    with pytest.raises(ConfigurationError):
        mgr.load_strict()


def test_load_strict_raises_on_broken_json(mgr):
    mgr.settings_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        mgr.load_strict()



def _make_settings_path_unreadable(mgr):
    if mgr.settings_path.exists():
        mgr.settings_path.unlink()
    mgr.settings_path.mkdir()


def test_load_strict_names_the_real_failure_when_the_file_is_unreadable(mgr):
    _make_settings_path_unreadable(mgr)
    with pytest.raises(ConfigurationError) as excinfo:
        mgr.load_strict()
    key, _, detail = str(excinfo.value).removeprefix("i18n:").partition("|")
    assert key == "err_settings_unreadable"
    assert key != "err_broken_json"
    assert "PermissionError" in detail


def test_ui_load_names_the_real_failure_when_the_file_is_unreadable(mgr):
    _make_settings_path_unreadable(mgr)
    errors, merged = load_ui(mgr)
    assert merged["JPEG_QUALITY"] == 90
    assert errors["general"]["type"] == "i18n"
    assert errors["general"]["value"] == "err_broken_json"
    assert "Broken JSON" not in errors["general"]["detail"]
    assert "PermissionError" in errors["general"]["detail"]


def test_ui_load_reports_broken_json_and_falls_back_to_defaults(mgr):
    mgr.settings_path.write_text("{ not json", encoding="utf-8")
    errors, merged = load_ui(mgr)
    assert merged["JPEG_QUALITY"] == 90
    assert errors["general"]["type"] == "i18n"
    assert errors["general"]["value"] == "err_broken_json"


def test_defaults_are_validated_like_user_values():
    fresh = Settings()
    assert isinstance(fresh.INPUT_FOLDER_PATH, Path)
    assert isinstance(fresh.OUTPUT_FOLDER_PATH, Path)
    assert Settings.model_config.get("validate_default") is True


def test_ui_load_valid_file(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(tmp_path)), encoding="utf-8")
    errors, merged = load_ui(mgr)
    assert errors == {}
    assert merged["OUTPUT_FOLDER_PATH"] == str(tmp_path / "output")


def test_save_excludes_env_tokens_and_next_load_sees_new_state(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(tmp_path)), encoding="utf-8")
    load_ui(mgr)
    obj = Settings(**base_paths(tmp_path, JPEG_QUALITY=42,
                                ENV_TOKENS={"custom": "secret"}))
    mgr.save_settings(obj)
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert "ENV_TOKENS" not in on_disk
    assert on_disk["JPEG_QUALITY"] == 42
    errors, merged = load_ui(mgr)
    assert merged["JPEG_QUALITY"] == 42
    assert errors == {}



def with_left_over(**entry_overrides) -> dict:
    values = ready_ai(**entry_overrides)
    values["LLM_PROVIDERS"][LEFT_OVER] = {"max_tokens": "abc"}
    return values


def test_garbage_in_non_selected_provider_is_ignored(mgr, tmp_path):
    errors, merged = validate(mgr, base_paths(tmp_path, **with_left_over()))
    assert errors == {}
    assert merged["LLM_PROVIDERS"][LEFT_OVER]["max_tokens"] == "abc"


def test_garbage_in_selected_provider_is_flagged_on_its_field(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(tmp_path, **ready_ai(max_tokens="abc")))
    assert set(errors) == {"LLM_PROVIDERS.custom.max_tokens"}


def test_ai_off_disables_all_provider_validation(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path,
        ENABLE_LLM_INFERENCE=False,
        LLM_PROVIDER="custom",
        LLM_PROVIDERS={"custom": {"max_tokens": "abc"}},
    ))
    assert errors == {}


def test_enabling_ai_surfaces_the_dormant_garbage(mgr, tmp_path):
    payload = base_paths(tmp_path, **ready_ai(max_tokens="abc"))
    errors_off, _ = validate(mgr, {**payload, "ENABLE_LLM_INFERENCE": False})
    errors_on, _ = validate(mgr, {**payload, "ENABLE_LLM_INFERENCE": True})
    assert errors_off == {}
    assert "LLM_PROVIDERS.custom.max_tokens" in errors_on


def test_load_strict_runs_despite_broken_non_selected_provider(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(tmp_path, **with_left_over())),
                                 encoding="utf-8")
    settings = mgr.load_strict()
    assert settings.LLM_PROVIDERS[LEFT_OVER]["max_tokens"] == "abc"
    assert settings.ACTIVE_PROVIDER_CONFIG.url == READY_ENTRY["url"]


def test_saving_preserves_non_selected_garbage_verbatim(mgr, tmp_path):
    settings_obj, errors, _ = mgr.validate_draft(base_paths(tmp_path, **with_left_over()))
    assert errors == {}
    mgr.save_settings(settings_obj)
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert on_disk["LLM_PROVIDERS"] == {"custom": READY_ENTRY, LEFT_OVER: {"max_tokens": "abc"}}
    from config_validator import settings_form_view
    view, _ = mgr.load_for_ui()
    providers = set(settings_form_view(view)["LLM_PROVIDERS"])
    assert "custom" in providers and LEFT_OVER not in providers



import pytest as _pytest

from config_validator import AI_TAB_FIELDS

SCALAR_GARBAGE_CASES = [
    ({"LLM_MAX_RETRIES": 0}, "LLM_MAX_RETRIES"),
    ({"LLM_MAX_RETRIES": "abc"}, "LLM_MAX_RETRIES"),
    ({"LLM_USER_PROMPT": ""}, "LLM_USER_PROMPT"),
    ({"LLM_PROVIDER": "nonsense"}, "LLM_PROVIDER"),
    ({"LLM_PROVIDERS": "not-even-a-dict"}, "LLM_PROVIDERS"),
]


@_pytest.mark.parametrize("overrides,field", SCALAR_GARBAGE_CASES)
def test_ai_off_scalar_garbage_is_dormant(mgr, tmp_path, overrides, field):
    errors, merged = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=False, **overrides))
    assert errors == {}, (overrides, errors)
    assert merged[field] == overrides[field]


@_pytest.mark.parametrize("overrides,field", SCALAR_GARBAGE_CASES)
def test_ai_on_surfaces_each_scalar_error(mgr, tmp_path, overrides, field):
    errors, _ = validate(mgr, base_paths(tmp_path, **{**ready_ai(), **overrides}))
    assert any(k == field or k.startswith(field + ".") for k in errors), \
        (overrides, errors)


def test_every_ai_tab_field_is_dormant_when_ai_off(mgr, tmp_path):
    assert AI_TAB_FIELDS, "derivation must find the AI tab"
    assert "ENABLE_LLM_INFERENCE" not in AI_TAB_FIELDS, \
        "the on/off switch itself must stay strictly validated"
    for field in sorted(AI_TAB_FIELDS):
        errors, _ = validate(mgr, base_paths(
            tmp_path, ENABLE_LLM_INFERENCE=False, **{field: "garbage!!"}))
        assert errors == {}, (field, errors)


def test_ai_off_apply_stores_garbage_verbatim(mgr, tmp_path):
    settings_obj, errors, _ = mgr.validate_draft(base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=False, LLM_MAX_RETRIES="abc"))
    assert errors == {}
    assert settings_obj is not None, "no errors must always mean a usable object"
    mgr.save_settings(settings_obj)
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert on_disk["LLM_MAX_RETRIES"] == "abc"


def test_ai_off_save_never_leaks_tokens(mgr, tmp_path):
    settings_obj, errors, _ = mgr.validate_draft(base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=False,
        ENV_TOKENS={"custom": "sk-secret"}))
    assert errors == {}
    mgr.save_settings(settings_obj)
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert "ENV_TOKENS" not in on_disk


def test_load_strict_runs_with_ai_garbage_on_disk(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=False, LLM_MAX_RETRIES="abc",
        LLM_PROVIDER="nonsense")), encoding="utf-8")
    settings = mgr.load_strict()
    assert isinstance(settings, Settings)
    assert settings.LLM_MAX_RETRIES == "abc"


def test_enabling_ai_surfaces_dormant_scalar_garbage(mgr, tmp_path):
    payload = base_paths(tmp_path, LLM_MAX_RETRIES="abc", **ready_ai())
    errors_off, _ = validate(mgr, {**payload, "ENABLE_LLM_INFERENCE": False})
    errors_on, _ = validate(mgr, {**payload, "ENABLE_LLM_INFERENCE": True})
    assert errors_off == {}
    assert "LLM_MAX_RETRIES" in errors_on


def test_ai_off_keeps_non_ai_errors(mgr, tmp_path):
    errors, _ = validate(mgr, {
        "INPUT_FOLDER_PATH": str(tmp_path / "does_not_exist"),
        "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
        "ENABLE_LLM_INFERENCE": False,
        "JPEG_QUALITY": 500,
        "LLM_MAX_RETRIES": "abc",
    })
    assert "JPEG_QUALITY" in errors
    assert "INPUT_FOLDER_PATH" in errors
    assert "LLM_MAX_RETRIES" not in errors


def test_ai_off_preserves_valid_ai_values(mgr, tmp_path):
    settings_obj, errors, _ = mgr.validate_draft(base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=False, LLM_MAX_RETRIES=7))
    assert errors == {}
    mgr.save_settings(settings_obj)
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert on_disk["LLM_MAX_RETRIES"] == 7



def test_no_retry_statuses_accepts_exactly_the_overall_result_vocabulary(
        mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path, NO_RETRY_STATUSES=["ok", "partial_fail", "fail", "skipped"]))
    assert "NO_RETRY_STATUSES" not in errors

    for bad_value in (["llm_partial"],
                      ["OK"],
                      ["ok", "Success"],
                      ["done"]):
        errors, _ = validate(mgr, base_paths(
            tmp_path, NO_RETRY_STATUSES=bad_value))
        assert "NO_RETRY_STATUSES" in errors, f"accepted {bad_value!r}"


def test_no_retry_checkbox_group_lists_exactly_the_enum_vocabulary():
    import re as _re

    from schemas import OverallResult

    template = (SRC / "templates" / "tabs" / "general_content.html").read_text(
        encoding="utf-8")
    match = _re.search(r"\{% for status in \[([^\]]+)\] %\}", template)
    assert match, "the NO_RETRY_STATUSES checkbox loop is gone from the template"
    listed = _re.findall(r"'([^']+)'", match.group(1))
    assert listed == [member.value for member in OverallResult]


PRESERVED_PROVIDER_ENTRIES = [
    {"max_tokens": "00123", "require_max_tokens": "false"},
    {"max_tokens": "abc", "future": {"empty": {}, "literal.key": [None, False, 9007199254740993]}},
    {}, None, [], "stored-for-later",
]


@pytest.mark.parametrize("ai_enabled", [False, True])
@pytest.mark.parametrize("entry", PRESERVED_PROVIDER_ENTRIES)
def test_inactive_provider_whole_entry_roundtrip(mgr, tmp_path, ai_enabled, entry):
    import copy
    raw = base_paths(tmp_path, **ready_ai())
    raw["ENABLE_LLM_INFERENCE"] = ai_enabled
    raw["LLM_PROVIDERS"][LEFT_OVER] = copy.deepcopy(entry)
    before = json.dumps(raw, sort_keys=True)
    obj, errors, _ = mgr.validate_draft(raw)
    assert errors == {}
    assert obj is not None
    mgr.save_settings(obj)
    saved = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert json.dumps(saved["LLM_PROVIDERS"], sort_keys=True) == json.dumps(
        {"custom": READY_ENTRY, LEFT_OVER: entry}, sort_keys=True)
    assert json.dumps(raw, sort_keys=True) == before
    loaded = mgr.load_strict()
    assert loaded.ACTIVE_PROVIDER_CONFIG.model
    assert isinstance(loaded.ACTIVE_PROVIDER_CONFIG.max_tokens, int)
    mgr.save_settings(loaded)
    assert json.loads(mgr.settings_path.read_text(encoding="utf-8")) == saved


@pytest.mark.parametrize("selected", ["custom", LEFT_OVER])
def test_ai_off_preserves_all_supplied_ai_data(mgr, tmp_path, selected):
    raw = base_paths(tmp_path, ENABLE_LLM_INFERENCE=False, LLM_PROVIDER=selected,
                     LLM_PROVIDERS={selected: {"max_tokens": "123", "require_max_tokens": "false"}},
                     LLM_MAX_RETRIES="003", HALT_ON_LLM_PARSE_ERROR="false", LLM_USER_PROMPT={"empty": {}})
    obj, errors, _ = mgr.validate_draft(raw)
    assert not errors and obj is not None
    mgr.save_settings(obj)
    saved = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    expected = {key: value for key, value in raw.items() if key in AI_TAB_FIELDS}
    actual = {key: value for key, value in saved.items() if key in AI_TAB_FIELDS}
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


@pytest.mark.parametrize("entry", [{"max_tokens": "abc"}, {"max_tokens": "123"}, {}])
def test_retirement_is_independent_of_provider_validation(mgr, tmp_path, monkeypatch, entry):
    import config_validator
    monkeypatch.setattr(config_validator, "RETIRED_PROVIDER_FIELDS", frozenset({"old_field"}))
    raw = base_paths(tmp_path, ENABLE_LLM_INFERENCE=False,
                     LLM_PROVIDERS={"custom": {**entry, "old_field": {"bad": []}, "future": {}}})
    obj, errors, _ = mgr.validate_draft(raw)
    assert not errors and obj is not None
    mgr.save_settings(obj)
    saved = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert saved["LLM_PROVIDERS"] == {"custom": {**entry, "future": {}}}


def test_explicit_assignment_after_loading_materializes_only_that_ai_field(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(tmp_path)), encoding="utf-8")
    settings = mgr.load_strict()
    settings.LLM_TIMEOUT_SECONDS = 42
    mgr.save_settings(settings)
    saved = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert saved["LLM_TIMEOUT_SECONDS"] == 42
    assert "LLM_PROVIDERS" not in saved
