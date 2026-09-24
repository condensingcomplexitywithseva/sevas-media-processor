# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import central_logger
import config_loader
import config_validator
from config_loader import ConfigManager, TokenManager
from config_validator import AI_TAB_FIELDS, ProviderConfig, Settings, SettingsAIDormant
from routes.web_server import SESSION_TOKEN, create_app
from schemas import ConfigurationError

SETTINGS_FIELDS = frozenset({
    "GUI_LANGUAGE", "INPUT_FOLDER_PATH", "OUTPUT_FOLDER_PATH", "LOGGING_LEVEL",
    "START_OVER", "NO_RETRY_STATUSES", "ENABLE_LLM_INFERENCE", "LLM_PROVIDER",
    "LLM_PROVIDERS", "LLM_USER_PROMPT", "LLM_USER_PROMPT_MODE", "LLM_SYSTEM_PROMPT",
    "LLM_SYSTEM_PROMPT_MODE", "LLM_OUTPUT_MODE", "LLM_OUTPUT_COLUMNS",
    "LLM_OUTPUT_COLUMN_INSTRUCTIONS", "LLM_JSON_MAX_ATTEMPTS", "LLM_ABORT_ON_MALFORMED_JSON",
    "MAX_JPEGS_PER_INFERENCE", "MAX_CONSECUTIVE_LLM_FAILURES", "HALT_ON_LLM_PARSE_ERROR",
    "LLM_MAX_RETRIES", "LLM_TIMEOUT_SECONDS", "LLM_RETRY_SLEEP_SECONDS", "ENV_TOKENS",
    "MAX_DIMENSION", "JPEG_QUALITY", "PDF_SCALE", "MAX_FILE_SIZE_KB", "LOWEST_QUALITY",
    "PILLOW_MAX_PIXELS", "WHITE_BACKGROUND", "OUTPUT_FILENAME_PREFIX_LENGTH",
    "OUTPUT_FILENAME_TIMESTAMPS", "VIDEO_MODE", "VIDEO_RANGE",
    "VIDEO_SUMMARY_TARGET_TOTAL_FRAMES", "VIDEO_SUMMARY_SCENE_SENSITIVITY",
    "VIDEO_SAMPLING_CAPTURE_RATE_FPS", "VIDEO_SAMPLING_MAX_FRAMES_BUDGET",
    "VIDEO_SAMPLING_SCENE_SENSITIVITY", "ANIMATION_RANGE", "ANIMATION_TARGET_TOTAL_FRAMES",
    "ANIMATION_SCENE_SENSITIVITY", "IMAGE_RANGE", "DOCUMENT_RANGE", "DOCUMENT_MAX_PAGES",
})
PROVIDER_FIELDS = frozenset({
    "url", "model", "auth_header_key", "auth_header_format", "extra_header_key",
    "extra_header_value", "require_max_tokens", "max_tokens", "max_tokens_field",
    "system_prompt_location", "image_payload_style", "reasoning_handling",
    "response_extraction_path",
})

AUTH = {"X-App-Token": SESSION_TOKEN}
FUTURE_TOP = {"SETTING_FROM_THE_FUTURE": 42, "ANOTHER_FUTURE_SWITCH": {"nested": ["x"]}}
FUTURE_FIELD = {"FUTURE_PROVIDER_FIELD": "keep me"}


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.endswith("_TOKEN"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(central_logger, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(central_logger, "_configured", False)
    m = ConfigManager(tmp_path)
    m.app_data_dir = tmp_path
    m.env_path = tmp_path / ".env"
    m.token_manager = TokenManager(m.env_path)
    monkeypatch.setattr(config_loader, "_manager", m)
    (tmp_path / "input").mkdir()
    return m


def write_settings(tmp_path, payload):
    (tmp_path / "settings.json").write_text(json.dumps(payload), encoding="utf-8")


def read_settings(tmp_path):
    return json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))


def old_file(tmp_path, **overrides) -> dict[str, Any]:
    payload = {
        "INPUT_FOLDER_PATH": str(tmp_path / "input"),
        "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
        "ENABLE_LLM_INFERENCE": True,
        "LLM_PROVIDER": "ollama",
        "LLM_PROVIDERS": {
            "ollama": {"url": "http://localhost:11434/v1/chat/completions", "model": "old-model"},
        },
    }
    payload.update(overrides)
    return payload


def newer_file(tmp_path, ai_enabled) -> dict[str, Any]:
    payload = old_file(tmp_path, ENABLE_LLM_INFERENCE=ai_enabled, **FUTURE_TOP)
    payload["LLM_PROVIDERS"]["ollama"].update(FUTURE_FIELD)
    payload["LLM_PROVIDERS"]["claude"] = {"model": "claude-future", **FUTURE_FIELD}
    payload["LLM_PROVIDERS"]["newprovider"] = {"url": "http://x", "model": "y", **FUTURE_FIELD}
    return payload


def flatten(ob, prefix=""):
    flat = {}
    for key, value in ob.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, name + "."))
        else:
            flat[name] = value
    return flat


def assert_future_keys_present(saved):
    for key, value in FUTURE_TOP.items():
        assert saved[key] == value, (key, saved.get(key))
    for provider in ("ollama", "claude", "newprovider"):
        assert saved["LLM_PROVIDERS"][provider]["FUTURE_PROVIDER_FIELD"] == "keep me", provider
    assert saved["LLM_PROVIDERS"]["newprovider"]["model"] == "y"



def test_every_setting_has_a_default():
    required = sorted(
        name for name, field in Settings.model_fields.items() if field.is_required()
    )
    assert required == [], (
        f"required settings with no default: {required} - a settings.json "
        "written before the field existed could no longer load"
    )


def test_an_older_settings_file_loads_with_defaults_for_the_missing_keys(mgr, tmp_path):
    write_settings(tmp_path, old_file(tmp_path))
    settings = mgr.load_strict()
    assert Settings.model_fields["JPEG_QUALITY"].default == settings.JPEG_QUALITY
    active = settings.ACTIVE_PROVIDER_CONFIG
    assert active.model == "old-model"
    assert active.max_tokens_field == ProviderConfig.model_fields["max_tokens_field"].default
    merged, errors = mgr.load_for_ui()
    assert errors == {}
    assert merged["LLM_PROVIDERS"]["ollama"]["model"] == "old-model"
    assert merged["JPEG_QUALITY"] == Settings.model_fields["JPEG_QUALITY"].default



@pytest.mark.parametrize("ai_enabled", [True, False], ids=["ai_on", "ai_off"])
def test_a_newer_file_loads_on_both_entry_points_with_no_error(mgr, tmp_path, ai_enabled):
    write_settings(tmp_path, newer_file(tmp_path, ai_enabled))
    merged, errors = mgr.load_for_ui()
    assert errors == {}
    assert merged["SETTING_FROM_THE_FUTURE"] == 42, "the server-side merged view carries the key"
    settings = mgr.load_strict()
    assert isinstance(settings, SettingsAIDormant if not ai_enabled else Settings)
    assert "SETTING_FROM_THE_FUTURE" not in Settings.model_fields
    assert settings.ACTIVE_PROVIDER_CONFIG.model == "old-model"


@pytest.mark.parametrize("ai_enabled", [True, False], ids=["ai_on", "ai_off"])
def test_the_loader_save_keeps_every_unknown_key_at_both_levels(mgr, tmp_path, ai_enabled):
    write_settings(tmp_path, newer_file(tmp_path, ai_enabled))
    mgr.save_settings(mgr.load_strict())
    saved = read_settings(tmp_path)
    assert_future_keys_present(saved)
    assert saved["LLM_PROVIDERS"]["ollama"]["model"] == "old-model"
    assert saved["ENABLE_LLM_INFERENCE"] is ai_enabled


@pytest.mark.parametrize("ai_enabled", [True, False], ids=["ai_on", "ai_off"])
def test_full_draft_api_keeps_every_unknown_key(mgr, tmp_path, ai_enabled):
    write_settings(tmp_path, newer_file(tmp_path, ai_enabled))
    merged, _ = mgr.load_for_ui()
    draft = flatten(merged)
    draft["JPEG_QUALITY"] = 77
    client = create_app().test_client()
    response = client.post("/api/settings/commit", json=draft, headers=AUTH)
    assert response.status_code == 200, response.get_json()
    saved = read_settings(tmp_path)
    assert saved["JPEG_QUALITY"] == 77
    assert_future_keys_present(saved)


def test_no_setting_name_contains_the_dot_the_window_uses_as_a_separator():
    names = list(Settings.model_fields) + list(ProviderConfig.model_fields)
    assert names and not [n for n in names if "." in n]


def test_reset_writes_the_defaults_and_drops_unknown_keys(mgr, tmp_path):
    write_settings(tmp_path, newer_file(tmp_path, True))
    client = create_app().test_client()
    response = client.post("/api/settings/reset", headers=AUTH)
    assert response.status_code == 200, response.get_json()
    saved = read_settings(tmp_path)
    assert "SETTING_FROM_THE_FUTURE" not in saved
    assert saved == json.loads(Settings().model_dump_json())


def test_a_newer_versions_provider_name_is_refused_on_the_visible_field(mgr, tmp_path):
    write_settings(tmp_path, newer_file(tmp_path, True) | {"LLM_PROVIDER": "newprovider"})
    merged, errors = mgr.load_for_ui()
    assert next(iter(errors)) == "LLM_PROVIDER", errors
    assert set(errors) <= {"LLM_PROVIDER", "ENV_TOKENS.newprovider"}, errors
    assert merged["LLM_PROVIDER"] == "newprovider", "the user's value is shown, not replaced"
    with pytest.raises(ConfigurationError) as refused:
        mgr.load_strict()
    assert refused.value.setting_field == "LLM_PROVIDER"


def test_a_known_key_whose_type_changed_is_refused_on_its_own_field(mgr, tmp_path):
    write_settings(tmp_path, newer_file(tmp_path, True)
                   | {"JPEG_QUALITY": "high", "LLM_MAX_RETRIES": "many"})
    _, errors = mgr.load_for_ui()
    assert set(errors) == {"JPEG_QUALITY", "LLM_MAX_RETRIES"}, errors
    assert "LLM_MAX_RETRIES" in AI_TAB_FIELDS

    write_settings(tmp_path, newer_file(tmp_path, False) | {"LLM_MAX_RETRIES": "many"})
    _, errors = mgr.load_for_ui()
    assert errors == {}
    assert read_settings(tmp_path)["LLM_MAX_RETRIES"] == "many"



@pytest.mark.parametrize("model, snapshot, retired_name", [
    (Settings, SETTINGS_FIELDS, "RETIRED_SETTINGS"),
    (ProviderConfig, PROVIDER_FIELDS, "RETIRED_PROVIDER_FIELDS"),
], ids=["Settings", "ProviderConfig"])
def test_a_removed_field_must_be_listed_as_retired(model, snapshot, retired_name):
    retired = getattr(config_validator, retired_name)
    current = frozenset(model.model_fields)
    removed = snapshot - current
    added = current - snapshot
    assert not (removed - retired), (
        f"{model.__name__} lost {sorted(removed - retired)}: a removed setting "
        f"must be added to config_validator.{retired_name} (so old files drop "
        "it on save) and taken out of the snapshot in this test"
    )
    assert not removed, (
        f"{sorted(removed)} left {model.__name__} and is retired; now remove it "
        "from the snapshot in this test"
    )
    assert not added, (
        f"{model.__name__} gained {sorted(added)}: add it to the snapshot in "
        "this test, so its own removal one day is not invisible"
    )
    assert not (retired & current), (
        f"{sorted(retired & current)} is both a live field and retired"
    )


def test_a_retired_key_is_dropped_on_save_while_a_future_key_is_kept(mgr, tmp_path, monkeypatch):
    monkeypatch.setattr(
        config_validator, "RETIRED_SETTINGS", config_validator.RETIRED_SETTINGS | {"OLD_SETTING"},
    )
    monkeypatch.setattr(
        config_validator, "RETIRED_PROVIDER_FIELDS", config_validator.RETIRED_PROVIDER_FIELDS | {"old_field"},
    )
    for ai_enabled in (True, False):
        payload = newer_file(tmp_path, ai_enabled)
        payload["OLD_SETTING"] = "gone soon"
        for provider in ("ollama", "claude"):
            payload["LLM_PROVIDERS"][provider]["old_field"] = "gone soon"
        write_settings(tmp_path, payload)
        _, errors = mgr.load_for_ui()
        assert errors == {}
        assert not hasattr(mgr.load_strict(), "OLD_SETTING")

        mgr.save_settings(mgr.load_strict())
        saved = read_settings(tmp_path)
        assert "OLD_SETTING" not in saved
        assert all("old_field" not in entry for entry in saved["LLM_PROVIDERS"].values())
        assert_future_keys_present(saved)

        write_settings(tmp_path, payload)
        draft = flatten(mgr.load_for_ui()[0])
        assert draft["OLD_SETTING"] == "gone soon", "the draft carries it invisibly until Apply"
        response = create_app().test_client().post("/api/settings/commit", json=draft, headers=AUTH)
        assert response.status_code == 200, response.get_json()
        saved = read_settings(tmp_path)
        assert "OLD_SETTING" not in saved
        assert all("old_field" not in entry for entry in saved["LLM_PROVIDERS"].values())
        assert_future_keys_present(saved)


def test_the_obsolete_export_toggle_is_explicitly_retired():
    assert "EXPORT_JOINED_SHEET" in config_validator.RETIRED_SETTINGS
    assert not config_validator.RETIRED_PROVIDER_FIELDS


@pytest.mark.parametrize("ai_enabled", [True, False], ids=["ai_on", "ai_off"])
@pytest.mark.parametrize("save_path", ["loader", "apply"])
@pytest.mark.parametrize("legacy_value", [
    True, False, "invalid", None, {"nested": [1, False]}, [], [False, {"future": 7}], 0, 2**64,
])
def test_retired_export_toggle_loads_unchanged_then_drops_on_save(
    mgr, tmp_path, ai_enabled, save_path, legacy_value,
):
    payload = newer_file(tmp_path, ai_enabled) | {"EXPORT_JOINED_SHEET": legacy_value}
    write_settings(tmp_path, payload)
    original = mgr.settings_path.read_bytes()

    merged, errors = mgr.load_for_ui()
    assert errors == {}
    settings = mgr.load_strict()
    assert "EXPORT_JOINED_SHEET" not in settings.model_dump()
    assert mgr.settings_path.read_bytes() == original, "loading must not migrate the file"

    if save_path == "loader":
        settings.JPEG_QUALITY = 77
        mgr.save_settings(settings)
    else:
        draft = flatten(merged)
        draft["JPEG_QUALITY"] = 77
        response = create_app().test_client().post("/api/settings/commit", json=draft, headers=AUTH)
        assert response.status_code == 200, response.get_json()

    saved = read_settings(tmp_path)
    assert "EXPORT_JOINED_SHEET" not in saved
    assert saved["JPEG_QUALITY"] == 77
    assert saved["ENABLE_LLM_INFERENCE"] is ai_enabled
    assert_future_keys_present(saved)
    assert "EXPORT_JOINED_SHEET" not in mgr.load_for_ui()[0]
    assert "EXPORT_JOINED_SHEET" not in mgr.load_strict().model_dump()


def test_positive_control_a_missing_field_is_what_is_required_means():
    from pydantic import BaseModel

    class Probe(BaseModel):
        with_default: int = 1
        without_default: int

    assert not Probe.model_fields["with_default"].is_required()
    assert Probe.model_fields["without_default"].is_required()


def test_positive_control_the_default_pydantic_policy_would_drop_the_keys():
    from pydantic import BaseModel

    class Dropping(BaseModel):
        known: int = 1

    assert "unknown" not in Dropping.model_validate({"known": 2, "unknown": 3}).model_dump()
    assert Settings.model_config.get("extra") == "allow"
    assert ProviderConfig.model_config.get("extra") == "allow"
    assert SettingsAIDormant.model_config.get("extra") == "allow"


@pytest.mark.parametrize("ai_enabled", [False, True])
def test_control_edits_preserve_opaque_data_and_missing_fields(mgr, tmp_path, ai_enabled):
    foreign = {"hasOwnProperty": {}, "__proto__": {"literal.key": 9007199254740993},
               "list": [False, None, {}, {"a.b": []}]}
    payload = old_file(tmp_path, ENABLE_LLM_INFERENCE=ai_enabled, **{"future.key": foreign})
    payload["LLM_PROVIDERS"]["claude"] = {"max_tokens": "00123", "require_max_tokens": "false", "future": foreign}
    payload["LLM_PROVIDERS"]["future.provider"] = foreign
    write_settings(tmp_path, payload)
    client = create_app().test_client()
    for quality in (81, 82):
        response = client.post("/api/settings/commit", json={"edits": {"JPEG_QUALITY": str(quality)}}, headers=AUTH)
        assert response.status_code == 200, response.get_json()
        saved = read_settings(tmp_path)
        assert saved["JPEG_QUALITY"] == quality
        assert saved["future.key"] == foreign
        assert saved["LLM_PROVIDERS"] == payload["LLM_PROVIDERS"]
        if ai_enabled:
            assert saved["LLM_MAX_RETRIES"] == 3
        else:
            assert "LLM_MAX_RETRIES" not in saved
        assert "future.key" not in response.get_json()["settings"]
        assert "future.provider" not in response.get_json()["settings"]["LLM_PROVIDERS"]


@pytest.mark.parametrize("edits", [[], None, {"LLM_PROVIDERS": {}}, {"ENV_TOKENS": {}},
                                  {"future": 1}, {"LLM_PROVIDERS.unknown.model": "x"},
                                  {"LLM_PROVIDERS.claude.unknown": 1}, {"ENV_TOKENS.unknown": "fake"}])
def test_unrecognized_edit_paths_are_refused_without_writes(mgr, tmp_path, edits):
    write_settings(tmp_path, old_file(tmp_path, ENABLE_LLM_INFERENCE=False))
    before = mgr.settings_path.read_bytes()
    response = create_app().test_client().post("/api/settings/commit", json={"edits": edits}, headers=AUTH)
    assert response.status_code == 400
    assert mgr.settings_path.read_bytes() == before
    assert not mgr.env_path.exists()


def test_edit_validation_is_atomic_and_tokens_stay_separate(mgr, tmp_path):
    write_settings(tmp_path, old_file(tmp_path))
    before = mgr.settings_path.read_bytes()
    client = create_app().test_client()
    changes = {"JPEG_QUALITY": "81", "LLM_PROVIDERS.ollama.max_tokens": "bad",
               "ENV_TOKENS.openai": "sk-fake0123456789abcdef0123456789abcdef"}
    response = client.post("/api/settings/commit", json={"edits": changes}, headers=AUTH)
    assert response.status_code == 400
    assert "LLM_PROVIDERS.ollama.max_tokens" in response.get_json()["errors"]
    assert mgr.settings_path.read_bytes() == before
    assert not mgr.env_path.exists()
    changes["LLM_PROVIDERS.ollama.max_tokens"] = "123"
    response = client.post("/api/settings/commit", json={"edits": changes}, headers=AUTH)
    assert response.status_code == 200
    saved = read_settings(tmp_path)
    assert "ENV_TOKENS" not in saved
    assert saved["LLM_PROVIDERS"]["ollama"]["max_tokens"] == 123
    assert mgr.get_env_tokens()["OPENAI_TOKEN"] == changes["ENV_TOKENS.openai"]
    for token in ("", "********"):
        response = client.post("/api/settings/commit", json={"edits": {"ENV_TOKENS.openai": token}}, headers=AUTH)
        assert response.status_code == 200
        assert mgr.get_env_tokens()["OPENAI_TOKEN"] == changes["ENV_TOKENS.openai"]


def test_edit_validation_refuses_corrupted_file(mgr):
    mgr.settings_path.write_text("{not json", encoding="utf-8")
    before = mgr.settings_path.read_bytes()
    response = create_app().test_client().post(
        "/api/settings/commit", json={"edits": {"JPEG_QUALITY": "81"}}, headers=AUTH)
    assert response.status_code == 400
    assert mgr.settings_path.read_bytes() == before


@pytest.mark.parametrize("restore_normalization", [False, True])
def test_preservation_assertion_detects_provider_normalization(mgr, tmp_path, monkeypatch, restore_normalization):
    from pydantic import field_validator

    class NormalizingDormant(SettingsAIDormant):
        @field_validator("LLM_PROVIDERS", mode="before")
        @classmethod
        def preserve_provider_data(cls, value):
            defaults = Settings().LLM_PROVIDERS
            return {key: ProviderConfig(**{**defaults.get(key, {}), **entry}).model_dump()
                    for key, entry in value.items()}

    if restore_normalization:
        monkeypatch.setattr(config_loader, "SettingsAIDormant", NormalizingDormant)
    entry = {"max_tokens": "123", "require_max_tokens": "false"}
    raw = old_file(tmp_path, ENABLE_LLM_INFERENCE=False, LLM_PROVIDERS={"claude": entry})
    obj, errors, _ = mgr.validate_draft(raw)
    assert not errors and obj is not None
    mgr.save_settings(obj)

    def assert_preserved():
        assert read_settings(tmp_path)["LLM_PROVIDERS"] == {"claude": entry}

    if restore_normalization:
        with pytest.raises(AssertionError):
            assert_preserved()
    else:
        assert_preserved()



def test_unrelated_edits_do_not_import_tokens_from_settings_file(mgr, tmp_path):
    write_settings(tmp_path, old_file(tmp_path, ENABLE_LLM_INFERENCE=False,
                                     ENV_TOKENS={"openai": "fake-not-a-token-update"}))
    response = create_app().test_client().post(
        "/api/settings/commit", json={"edits": {"JPEG_QUALITY": "81"}}, headers=AUTH)
    assert response.status_code == 200
    assert "ENV_TOKENS" not in read_settings(tmp_path)
    assert not mgr.env_path.exists()
