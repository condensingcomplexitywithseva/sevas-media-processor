# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import os
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from config_loader import ConfigManager
from schemas import ConfigurationError
from config_validator import Settings



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
    monkeypatch.setattr(m, "get_env_tokens", lambda: {})
    for key in list(os.environ):
        if key.endswith("_TOKEN"):
            monkeypatch.delenv(key)
    (tmp_path / "input").mkdir()
    return m


def base_paths(tmp_path, **overrides):
    payload = {
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


def test_llm_enabled_requires_token(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai"))
    assert errors.get("ENV_TOKENS.openai") == {"type": "i18n", "value": "err_missing_token"}
    assert "ENABLE_LLM_INFERENCE" not in errors


def test_llm_incomplete_ai_never_blames_mode_field(mgr, tmp_path):
    cases = [
        (dict(LLM_PROVIDER="custom"), "LLM_PROVIDERS.custom.url"),
        (dict(LLM_PROVIDER="custom"), "LLM_PROVIDERS.custom.model"),
        (dict(LLM_PROVIDER="ollama", LLM_USER_PROMPT=" "), "LLM_USER_PROMPT"),
        (dict(LLM_PROVIDER="ollama", LLM_SYSTEM_PROMPT=" "), "LLM_SYSTEM_PROMPT"),
        (dict(LLM_PROVIDER="openai"), "ENV_TOKENS.openai"),
    ]
    for overrides, expected_key in cases:
        errors, _ = validate(mgr, base_paths(
            tmp_path, ENABLE_LLM_INFERENCE=True, **overrides))
        assert expected_key in errors, (overrides, errors)
        assert "ENABLE_LLM_INFERENCE" not in errors, (overrides, errors)


def test_llm_local_provider_fully_configured_is_clean(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="ollama"))
    assert errors == {}


def test_llm_mode_type_error_still_surfaces(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE="banana", LLM_PROVIDER="openai"))
    assert "ENABLE_LLM_INFERENCE" in errors


def test_load_strict_blocks_run_on_missing_token(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai")),
        encoding="utf-8")
    with pytest.raises(ConfigurationError, match="ENV_TOKENS"):
        mgr.load_strict()


def test_draft_empty_token_string_means_no_change(mgr, tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "get_env_tokens", lambda: {"OPENAI_TOKEN": "stored-secret"})
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai",
        ENV_TOKENS={"openai": ""}))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_masked_token_value_means_no_change(mgr, tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "get_env_tokens", lambda: {"OPENAI_TOKEN": "stored-secret"})
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai",
        ENV_TOKENS={"openai": "********"}))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_draft_token_entry_satisfies_validation(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai",
        ENV_TOKENS={"openai": "sk-new"}))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_environment_variable_counts_as_token(mgr, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_TOKEN", "from-env")
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai"))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_llm_local_provider_needs_no_token(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="ollama"))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_llm_prompt_file_missing_flagged(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path,
        ENABLE_LLM_INFERENCE=True,
        LLM_PROVIDER="ollama",
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
        ENABLE_LLM_INFERENCE=True,
        LLM_PROVIDER="ollama",
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
        ENABLE_LLM_INFERENCE=True,
        LLM_PROVIDER="ollama",
        LLM_USER_PROMPT_MODE="FILE",
        LLM_USER_PROMPT=str(prompt),
    ))
    assert not any(k.startswith("LLM_USER_PROMPT") for k in errors), errors
    assert prompt in recorded


def test_llm_disabled_skips_ai_checks(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=False, LLM_PROVIDER="openai"))
    assert errors == {}


def test_update_tokens_empty_string_deletes(tmp_path, monkeypatch):
    from config_loader import TokenManager
    monkeypatch.delenv("OPENAI_TOKEN", raising=False)
    tm = TokenManager(tmp_path / ".env")
    tm.update_tokens({"openai": "sk-x"})
    assert tm.get_tokens() == {"OPENAI_TOKEN": "sk-x"}
    assert os.environ.get("OPENAI_TOKEN") == "sk-x"
    tm.update_tokens({"openai": ""})
    assert tm.get_tokens() == {}
    assert "OPENAI_TOKEN" not in os.environ



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
    assert "could not be read" in str(excinfo.value)
    assert "corrupted" not in str(excinfo.value)
    assert "PermissionError" in str(excinfo.value)


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


def test_ui_load_valid_file(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(tmp_path)), encoding="utf-8")
    errors, merged = load_ui(mgr)
    assert errors == {}
    assert merged["OUTPUT_FOLDER_PATH"] == str(tmp_path / "output")


def test_save_excludes_env_tokens_and_next_load_sees_new_state(mgr, tmp_path):
    mgr.settings_path.write_text(json.dumps(base_paths(tmp_path)), encoding="utf-8")
    load_ui(mgr)
    obj = Settings(**base_paths(tmp_path, JPEG_QUALITY=42,
                                ENV_TOKENS={"openai": "secret"}))
    mgr.save_settings(obj)
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert "ENV_TOKENS" not in on_disk
    assert on_disk["JPEG_QUALITY"] == 42
    errors, merged = load_ui(mgr)
    assert merged["JPEG_QUALITY"] == 42
    assert errors == {}



def test_garbage_in_non_selected_provider_is_ignored(mgr, tmp_path):
    errors, merged = validate(mgr, base_paths(
        tmp_path,
        ENABLE_LLM_INFERENCE=True,
        LLM_PROVIDER="openai",
        LLM_PROVIDERS={"claude": {"max_tokens": "abc"}},
        ENV_TOKENS={"openai": "sk-fake0123456789abcdef0123456789abcdef"},
    ))
    assert errors == {}
    assert merged["LLM_PROVIDERS"]["claude"]["max_tokens"] == "abc"


def test_garbage_in_selected_provider_is_flagged_on_its_field(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path,
        ENABLE_LLM_INFERENCE=True,
        LLM_PROVIDER="claude",
        LLM_PROVIDERS={"claude": {"max_tokens": "abc"}},
        ENV_TOKENS={"claude": "sk-ant-fake0123456789abcdefABCDEF"},
    ))
    assert "LLM_PROVIDERS.claude.max_tokens" in errors
    assert "ENV_TOKENS.claude" not in errors


def test_ai_off_disables_all_provider_validation(mgr, tmp_path):
    errors, _ = validate(mgr, base_paths(
        tmp_path,
        ENABLE_LLM_INFERENCE=False,
        LLM_PROVIDER="claude",
        LLM_PROVIDERS={"claude": {"max_tokens": "abc"}},
    ))
    assert errors == {}


def test_enabling_ai_surfaces_the_dormant_garbage(mgr, tmp_path):
    payload = base_paths(
        tmp_path,
        LLM_PROVIDER="claude",
        LLM_PROVIDERS={"claude": {"max_tokens": "abc"}},
        ENV_TOKENS={"claude": "sk-ant-fake0123456789abcdefABCDEF"},
    )
    errors_off, _ = validate(mgr, {**payload, "ENABLE_LLM_INFERENCE": False})
    errors_on, _ = validate(mgr, {**payload, "ENABLE_LLM_INFERENCE": True})
    assert errors_off == {}
    assert "LLM_PROVIDERS.claude.max_tokens" in errors_on


def test_load_strict_runs_despite_broken_non_selected_provider(mgr, tmp_path):
    mgr.get_env_tokens = lambda: {"OPENAI_TOKEN": "sk-fake0123456789abcdef0123456789abcdef"}
    mgr.settings_path.write_text(json.dumps(base_paths(
        tmp_path,
        ENABLE_LLM_INFERENCE=True,
        LLM_PROVIDER="openai",
        LLM_PROVIDERS={"claude": {"max_tokens": "abc"}},
    )), encoding="utf-8")
    settings = mgr.load_strict()
    assert settings.LLM_PROVIDERS["claude"]["max_tokens"] == "abc"
    assert settings.ACTIVE_PROVIDER_CONFIG.url == "https://api.openai.com/v1/chat/completions"


def test_saving_preserves_non_selected_garbage_verbatim(mgr, tmp_path):
    settings_obj, errors, _ = mgr.validate_draft(base_paths(
        tmp_path,
        ENABLE_LLM_INFERENCE=True,
        LLM_PROVIDER="openai",
        LLM_PROVIDERS={"claude": {"max_tokens": "abc"}},
        ENV_TOKENS={"openai": "sk-fake0123456789abcdef0123456789abcdef"},
    ))
    assert errors == {}
    mgr.save_settings(settings_obj)
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert on_disk["LLM_PROVIDERS"]["claude"]["max_tokens"] == "abc"
    assert set(on_disk["LLM_PROVIDERS"]) >= {
        "openai", "claude", "gemini", "deepseek", "mistral",
        "ollama", "lm-studio", "custom",
    }



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
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True,
        ENV_TOKENS={"openai": "sk-fake0123456789abcdef0123456789abcdef"}, **overrides))
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
        ENV_TOKENS={"openai": "sk-secret"}))
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
    payload = base_paths(tmp_path, LLM_MAX_RETRIES="abc",
                         ENV_TOKENS={"openai": "sk-fake0123456789abcdef0123456789abcdef"})
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
