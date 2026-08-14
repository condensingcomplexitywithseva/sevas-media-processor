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

import config_loader
from config_loader import ConfigManager, TokenManager



@pytest.fixture
def mgr(tmp_path, monkeypatch):
    m = ConfigManager(tmp_path)
    m.token_manager = TokenManager(tmp_path / ".env")
    for key in list(os.environ):
        if key.endswith("_TOKEN"):
            monkeypatch.delenv(key)
    (tmp_path / "input").mkdir()
    monkeypatch.setattr(config_loader, "_manager", m)
    return m


@pytest.fixture
def client(mgr):
    from routes.web_server import create_app, SESSION_TOKEN

    app = create_app()
    app.testing = True
    test_client = app.test_client()

    def post_json(url, payload):
        return test_client.post(
            url, json=payload, headers={"X-App-Token": SESSION_TOKEN}
        )

    test_client.post_json = post_json
    return test_client


def base_paths(tmp_path, **overrides):
    payload = {
        "INPUT_FOLDER_PATH": str(tmp_path / "input"),
        "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
    }
    payload.update(overrides)
    return payload



def _emitted_error_keys():
    key_pattern = re.compile(r"[\"':](err_[a-z_{}]+)")
    keys = set()
    for py_file in list(SRC.glob("*.py")) + list((SRC / "routes").glob("*.py")):
        for match in key_pattern.findall(py_file.read_text(encoding="utf-8")):
            if "{prefix}" in match:
                keys.add(match.replace("{prefix}", "user"))
                keys.add(match.replace("{prefix}", "sys"))
            elif "{" not in match:
                keys.add(match)
    return keys


@pytest.mark.parametrize(
    "lang", sorted(p.stem for p in (SRC / "locales").glob("*.json"))
)
def test_every_emitted_error_key_is_translated(lang):
    keys = _emitted_error_keys()
    assert len(keys) >= 15, f"source scan looks broken, found only: {sorted(keys)}"

    locale = json.loads(
        (SRC / "locales" / f"{lang}.json").read_text(encoding="utf-8")
    )
    missing = sorted(k for k in keys if k not in locale)
    assert not missing, f"error keys with no {lang} translation: {missing}"



def test_business_errors_identical_with_and_without_type_errors(mgr, tmp_path):
    bad_input = {
        "INPUT_FOLDER_PATH": str(tmp_path / "does_not_exist"),
        "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
    }
    _, errors_types_ok, _ = mgr.validate_draft(bad_input)
    _, errors_types_bad, _ = mgr.validate_draft({**bad_input, "JPEG_QUALITY": 500})

    assert errors_types_ok["INPUT_FOLDER_PATH"] == errors_types_bad["INPUT_FOLDER_PATH"]
    assert set(errors_types_bad) == set(errors_types_ok) | {"JPEG_QUALITY"}


def test_business_errors_identical_for_ai_rules_too(mgr, tmp_path):
    payload = base_paths(tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai")
    _, errors_types_ok, _ = mgr.validate_draft(payload)
    _, errors_types_bad, _ = mgr.validate_draft({**payload, "MAX_DIMENSION": "abc"})

    assert errors_types_ok["ENV_TOKENS.openai"] == errors_types_bad["ENV_TOKENS.openai"]
    assert set(errors_types_bad) == set(errors_types_ok) | {"MAX_DIMENSION"}



def test_cold_and_warm_reads_agree(mgr, tmp_path):
    mgr.settings_path.write_text(
        json.dumps(base_paths(tmp_path, JPEG_QUALITY=42)), encoding="utf-8"
    )
    warm_first = mgr.load_for_ui()
    warm_second = mgr.load_for_ui()

    cold_mgr = ConfigManager(tmp_path)
    cold_mgr.token_manager = TokenManager(tmp_path / ".env")
    cold = cold_mgr.load_for_ui()

    assert warm_first == warm_second == cold


def test_hand_edit_on_disk_is_visible_on_next_load(mgr, tmp_path):
    mgr.settings_path.write_text(
        json.dumps(base_paths(tmp_path, JPEG_QUALITY=42)), encoding="utf-8"
    )
    merged, _ = mgr.load_for_ui()
    assert merged["JPEG_QUALITY"] == 42

    mgr.settings_path.write_text(
        json.dumps(base_paths(tmp_path, JPEG_QUALITY=77)), encoding="utf-8"
    )
    merged, _ = mgr.load_for_ui()
    assert merged["JPEG_QUALITY"] == 77



def test_validate_draft_touches_neither_disk_nor_cache(mgr, tmp_path):
    mgr.settings_path.write_text(
        json.dumps(base_paths(tmp_path, JPEG_QUALITY=42)), encoding="utf-8"
    )
    disk_before = mgr.settings_path.read_bytes()
    ui_before = mgr.load_for_ui()

    mgr.validate_draft(base_paths(tmp_path, JPEG_QUALITY=77))
    mgr.validate_draft(base_paths(tmp_path, JPEG_QUALITY=500))

    assert mgr.settings_path.read_bytes() == disk_before
    assert mgr.load_for_ui() == ui_before



def test_error_catalogue_exact_shapes(mgr, tmp_path):
    a_file = tmp_path / "a_file.txt"
    a_file.write_text("x", encoding="utf-8")
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("", encoding="utf-8")
    missing = str(tmp_path / "does_not_exist")
    ai = dict(ENABLE_LLM_INFERENCE=True,
              ENV_TOKENS={"openai": "sk-fake0123456789abcdef0123456789abcdef"})

    cases = [
        ({"IMAGE_RANGE": "abc"}, "IMAGE_RANGE",
         {"type": "i18n", "value": "err_invalid_range"}),
        ({"VIDEO_RANGE": "abc"}, "VIDEO_RANGE",
         {"type": "i18n", "value": "err_invalid_time_range"}),
        ({"WHITE_BACKGROUND": "abc"}, "WHITE_BACKGROUND",
         {"type": "i18n", "value": "err_white_bg_format"}),
        ({"WHITE_BACKGROUND": [300, 0, 0]}, "WHITE_BACKGROUND",
         {"type": "i18n", "value": "err_rgb_range"}),
        ({"JPEG_QUALITY": 50, "LOWEST_QUALITY": 80}, "LOWEST_QUALITY",
         {"type": "i18n", "value": "err_quality_floor_above_start"}),
        ({"MAX_DIMENSION": "abc"}, "MAX_DIMENSION",
         {"type": "i18n", "value": "err_valid_integer"}),
        ({"VIDEO_SUMMARY_SCENE_SENSITIVITY": "abc"}, "VIDEO_SUMMARY_SCENE_SENSITIVITY",
         {"type": "i18n", "value": "err_valid_number"}),
        ({"JPEG_QUALITY": 500}, "JPEG_QUALITY", {"type": "max_bound", "value": "100"}),
        ({"JPEG_QUALITY": 0}, "JPEG_QUALITY", {"type": "min_bound", "value": "1"}),
        ({"INPUT_FOLDER_PATH": missing}, "INPUT_FOLDER_PATH",
         {"type": "i18n", "value": "err_input_missing"}),
        ({"INPUT_FOLDER_PATH": str(a_file)}, "INPUT_FOLDER_PATH",
         {"type": "i18n", "value": "err_input_not_dir"}),
        ({"OUTPUT_FOLDER_PATH": str(tmp_path / "input" / "sub")}, "OUTPUT_FOLDER_PATH",
         {"type": "i18n", "value": "err_path_overlap"}),
        ({**ai, "LLM_PROVIDER": "custom"}, "LLM_PROVIDERS.custom.url",
         {"type": "i18n", "value": "err_missing_url"}),
        ({**ai, "LLM_PROVIDER": "custom"}, "LLM_PROVIDERS.custom.model",
         {"type": "i18n", "value": "err_missing_model"}),
        ({**ai, "LLM_PROVIDER": "openai", "ENV_TOKENS": {}}, "ENV_TOKENS.openai",
         {"type": "i18n", "value": "err_missing_token"}),
        ({**ai, "LLM_PROVIDERS": "junk"}, "LLM_PROVIDER",
         {"type": "i18n", "value": "err_fix_ai_settings"}),
        ({**ai, "LLM_USER_PROMPT": "  "}, "LLM_USER_PROMPT",
         {"type": "i18n", "value": "err_missing_prompt"}),
        ({**ai, "LLM_USER_PROMPT_MODE": "FILE", "LLM_USER_PROMPT": missing},
         "LLM_USER_PROMPT", {"type": "i18n", "value": "err_user_file_missing"}),
        ({**ai, "LLM_USER_PROMPT_MODE": "FILE", "LLM_USER_PROMPT": str(empty_file)},
         "LLM_USER_PROMPT", {"type": "i18n", "value": "err_user_file_empty"}),
        ({**ai, "LLM_SYSTEM_PROMPT_MODE": "FILE", "LLM_SYSTEM_PROMPT": missing},
         "LLM_SYSTEM_PROMPT", {"type": "i18n", "value": "err_sys_file_missing"}),
        ({**ai, "LLM_SYSTEM_PROMPT_MODE": "FILE", "LLM_SYSTEM_PROMPT": str(empty_file)},
         "LLM_SYSTEM_PROMPT", {"type": "i18n", "value": "err_sys_file_empty"}),
    ]
    for overrides, field, expected in cases:
        _, errors, _ = mgr.validate_draft(base_paths(tmp_path, **overrides))
        assert errors.get(field) == expected, (overrides, field, errors)



def test_commit_valid_draft_lands_on_disk(client, mgr, tmp_path):
    resp = client.post_json(
        "/api/settings/commit", base_paths(tmp_path, JPEG_QUALITY=55)
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "success"
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert on_disk["JPEG_QUALITY"] == 55


def test_commit_accepts_flattened_dot_keys(client, mgr, tmp_path):
    payload = base_paths(tmp_path)
    payload["LLM_PROVIDERS.custom.url"] = "http://example.test/v1"
    resp = client.post_json("/api/settings/commit", payload)
    assert resp.status_code == 200
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert on_disk["LLM_PROVIDERS"]["custom"]["url"] == "http://example.test/v1"


def test_commit_is_all_or_nothing(client, mgr, tmp_path):
    mgr.settings_path.write_text(
        json.dumps(base_paths(tmp_path, JPEG_QUALITY=42, MAX_DIMENSION=1000)),
        encoding="utf-8",
    )
    resp = client.post_json(
        "/api/settings/commit",
        base_paths(tmp_path, JPEG_QUALITY=55, MAX_DIMENSION="abc"),
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["status"] == "error"
    assert "MAX_DIMENSION" in body["errors"]

    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert on_disk["JPEG_QUALITY"] == 42
    assert on_disk["MAX_DIMENSION"] == 1000


def test_commit_never_writes_tokens_to_settings_json(client, mgr, tmp_path):
    resp = client.post_json(
        "/api/settings/commit",
        base_paths(tmp_path, ENV_TOKENS={"openai": "sk-secret"}),
    )
    assert resp.status_code == 200
    on_disk = json.loads(mgr.settings_path.read_text(encoding="utf-8"))
    assert "ENV_TOKENS" not in on_disk
    assert "sk-secret" not in mgr.settings_path.read_text(encoding="utf-8")


def test_commit_new_token_lands_in_env_file(client, mgr, tmp_path):
    resp = client.post_json(
        "/api/settings/commit",
        base_paths(
            tmp_path,
            ENABLE_LLM_INFERENCE=True,
            LLM_PROVIDER="openai",
            ENV_TOKENS={"openai": "sk-new-token"},
        ),
    )
    assert resp.status_code == 200
    assert mgr.token_manager.get_tokens() == {"OPENAI_TOKEN": "sk-new-token"}
    assert resp.get_json()["env_tokens"] == {"openai": "********"}


@pytest.mark.parametrize("echoed_value", ["", "********"])
def test_commit_masked_or_empty_token_changes_nothing(client, mgr, tmp_path, echoed_value):
    mgr.token_manager.update_tokens({"openai": "sk-stored"})
    resp = client.post_json(
        "/api/settings/commit",
        base_paths(
            tmp_path,
            ENABLE_LLM_INFERENCE=True,
            LLM_PROVIDER="openai",
            ENV_TOKENS={"openai": echoed_value},
        ),
    )
    assert resp.status_code == 200, resp.get_json()
    assert mgr.token_manager.get_tokens() == {"OPENAI_TOKEN": "sk-stored"}


def test_real_token_updates_truth_table():
    from config_loader import real_token_updates

    assert real_token_updates({
        "openai": "sk-real",
        "claude": "",
        "gemini": "   ",
        "mistral": "********",
        "deepseek": None,
        "ollama": "  sk-padded  ",
    }) == {"openai": "sk-real", "ollama": "sk-padded"}

    assert real_token_updates(None) == {}
    assert real_token_updates("not-a-dict") == {}
    assert real_token_updates(42) == {}


def test_wipe_endpoint_deletes_immediately_and_reports_errors(client, mgr, tmp_path):
    mgr.token_manager.update_tokens({"openai": "sk-stored"})
    mgr.settings_path.write_text(
        json.dumps(base_paths(
            tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai")),
        encoding="utf-8",
    )
    resp = client.post_json("/api/settings/wipe_token", {"provider": "openai"})
    assert resp.status_code == 200
    assert mgr.token_manager.get_tokens() == {}
    body = resp.get_json()
    assert body["errors"].get("ENV_TOKENS.openai") == {
        "type": "i18n", "value": "err_missing_token",
    }
    assert body["env_tokens"] == {}
