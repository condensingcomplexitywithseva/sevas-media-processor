# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import copy
import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from schemas import ConfigurationError
from test_settings_validation import PRESERVED_PROVIDER_ENTRIES, base_paths, mgr, validate  # noqa: F401


def test_llm_enabled_requires_token(mgr, tmp_path):  # noqa: F811 (fixture)
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai"))
    assert errors.get("ENV_TOKENS.openai") == {"type": "i18n", "value": "err_missing_token"}
    assert "ENABLE_LLM_INFERENCE" not in errors


def test_a_missing_token_never_blames_the_mode_field(mgr, tmp_path):  # noqa: F811 (fixture)
    errors, _ = validate(mgr, base_paths(tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai"))
    assert "ENV_TOKENS.openai" in errors
    assert "ENABLE_LLM_INFERENCE" not in errors


def test_llm_local_provider_fully_configured_is_clean(mgr, tmp_path):  # noqa: F811 (fixture)
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="ollama"))
    assert errors == {}


def test_load_strict_blocks_run_on_missing_token(mgr, tmp_path):  # noqa: F811 (fixture)
    mgr.settings_path.write_text(json.dumps(base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai")),
        encoding="utf-8")
    with pytest.raises(ConfigurationError) as refused:
        mgr.load_strict()
    assert refused.value.setting_field == "ENV_TOKENS.openai"


def test_draft_empty_token_string_means_no_change(mgr, tmp_path, monkeypatch):  # noqa: F811 (fixture)
    monkeypatch.setattr(mgr, "get_env_tokens", lambda: {"OPENAI_TOKEN": "stored-secret"})
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai",
        ENV_TOKENS={"openai": ""}))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_masked_token_value_means_no_change(mgr, tmp_path, monkeypatch):  # noqa: F811 (fixture)
    monkeypatch.setattr(mgr, "get_env_tokens", lambda: {"OPENAI_TOKEN": "stored-secret"})
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai",
        ENV_TOKENS={"openai": "********"}))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_draft_token_entry_satisfies_validation(mgr, tmp_path):  # noqa: F811 (fixture)
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai",
        ENV_TOKENS={"openai": "sk-new"}))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_environment_variable_counts_as_token(mgr, tmp_path, monkeypatch):  # noqa: F811 (fixture)
    monkeypatch.setenv("OPENAI_TOKEN", "from-env")
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai"))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_llm_local_provider_needs_no_token(mgr, tmp_path):  # noqa: F811 (fixture)
    errors, _ = validate(mgr, base_paths(
        tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="ollama"))
    assert not any(k.startswith("ENV_TOKENS") for k in errors), errors


def test_garbage_in_non_selected_preset_is_ignored(mgr, tmp_path):  # noqa: F811 (fixture)
    errors, merged = validate(mgr, base_paths(
        tmp_path,
        ENABLE_LLM_INFERENCE=True,
        LLM_PROVIDER="openai",
        LLM_PROVIDERS={"claude": {"max_tokens": "abc"}},
        ENV_TOKENS={"openai": "sk-fake0123456789abcdef0123456789abcdef"},
    ))
    assert errors == {}
    assert merged["LLM_PROVIDERS"]["claude"]["max_tokens"] == "abc"


def test_garbage_in_selected_preset_is_flagged_on_its_field(mgr, tmp_path):  # noqa: F811 (fixture)
    errors, _ = validate(mgr, base_paths(
        tmp_path,
        ENABLE_LLM_INFERENCE=True,
        LLM_PROVIDER="claude",
        LLM_PROVIDERS={"claude": {"max_tokens": "abc"}},
        ENV_TOKENS={"claude": "sk-ant-fake0123456789abcdefABCDEF"},
    ))
    assert "LLM_PROVIDERS.claude.max_tokens" in errors
    assert "ENV_TOKENS.claude" not in errors


def test_load_strict_runs_despite_broken_non_selected_preset(mgr, tmp_path):  # noqa: F811 (fixture)
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


def test_the_form_shows_every_preset(mgr, tmp_path):  # noqa: F811 (fixture)
    from config_validator import settings_form_view
    mgr.settings_path.write_text(json.dumps(base_paths(
        tmp_path, LLM_PROVIDERS={"claude": {"max_tokens": "abc"}})), encoding="utf-8")
    view, _ = mgr.load_for_ui()
    assert set(settings_form_view(view)["LLM_PROVIDERS"]) == {
        "openai", "claude", "gemini", "deepseek", "mistral", "zai", "ollama", "lm-studio", "custom",
    }


@pytest.mark.parametrize("provider",
                         ["openai", "claude", "gemini", "deepseek", "mistral", "zai", "ollama", "lm-studio",
                          "custom"])
@pytest.mark.parametrize("ai_enabled", [False, True])
@pytest.mark.parametrize("entry", PRESERVED_PROVIDER_ENTRIES)
def test_inactive_preset_whole_entry_roundtrip(mgr, tmp_path, provider, ai_enabled, entry):  # noqa: F811 (fixture)
    selected = "lm-studio" if provider == "ollama" else "ollama"
    raw = base_paths(tmp_path, ENABLE_LLM_INFERENCE=ai_enabled, LLM_PROVIDER=selected,
                     LLM_PROVIDERS={provider: copy.deepcopy(entry)})
    before = json.dumps(raw, sort_keys=True)
    obj, errors, _ = mgr.validate_draft(raw)
    assert errors == {}
    assert obj is not None
    mgr.save_settings(obj)
    saved = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert json.dumps(saved["LLM_PROVIDERS"], sort_keys=True) == json.dumps({provider: entry}, sort_keys=True)
    assert json.dumps(raw, sort_keys=True) == before
    loaded = mgr.load_strict()
    assert loaded.ACTIVE_PROVIDER_CONFIG.model
    assert isinstance(loaded.ACTIVE_PROVIDER_CONFIG.max_tokens, int)
    mgr.save_settings(loaded)
    assert json.loads(mgr.settings_path.read_text(encoding="utf-8")) == saved
