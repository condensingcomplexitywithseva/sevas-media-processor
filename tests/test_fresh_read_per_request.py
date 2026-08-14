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

import central_logger
import config_loader
from config_loader import ConfigManager, TokenManager
from routes.web_server import create_app, SESSION_TOKEN

AUTH = {"X-App-Token": SESSION_TOKEN}

RAW_STATE = re.compile(r"window\.rawOriginalState = (\{.*\});")
DISK_ERRORS = re.compile(r"window\.diskErrors = (\{.*\});")


@pytest.fixture
def app(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.endswith("_TOKEN"):
            monkeypatch.delenv(key)

    monkeypatch.setattr(central_logger, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(central_logger, "_configured", False)

    mgr = ConfigManager(tmp_path)
    mgr.app_data_dir = tmp_path
    mgr.env_path = tmp_path / ".env"
    mgr.token_manager = TokenManager(tmp_path / ".env")
    (tmp_path / "input").mkdir()
    monkeypatch.setattr(config_loader, "_manager", mgr)

    application = create_app()
    application.testing = True
    return application


def write_settings(tmp_path, **overrides):
    payload = {
        "INPUT_FOLDER_PATH": str(tmp_path / "input"),
        "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
    }
    payload.update(overrides)
    (tmp_path / "settings.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def page_globals(client):
    html = client.get("/").get_data(as_text=True)
    state = RAW_STATE.search(html)
    errors = DISK_ERRORS.search(html)
    assert state and errors, "the page no longer embeds the frontend state globals"
    return json.loads(state.group(1)), json.loads(errors.group(1))


def test_hand_edit_shows_up_on_the_next_page_request(app, tmp_path):
    client = app.test_client()

    write_settings(tmp_path, JPEG_QUALITY=42)
    state, _ = page_globals(client)
    assert state["JPEG_QUALITY"] == 42

    write_settings(tmp_path, JPEG_QUALITY=77)
    state, _ = page_globals(client)
    assert state["JPEG_QUALITY"] == 77, "the page served a remembered value"


def test_validation_state_is_recomputed_on_every_page_request(app, tmp_path):
    client = app.test_client()

    write_settings(tmp_path, JPEG_QUALITY=42)
    _, errors = page_globals(client)
    assert errors == {}, f"clean settings must produce no disk errors, got {errors}"

    write_settings(tmp_path, JPEG_QUALITY=500)
    state, errors = page_globals(client)
    assert errors.get("JPEG_QUALITY") == {"type": "max_bound", "value": "100"}
    assert state["JPEG_QUALITY"] == 500, "the offending value must render as typed"

    write_settings(tmp_path, JPEG_QUALITY=42)
    _, errors = page_globals(client)
    assert errors == {}, "the error survived a repair of the file on disk"


def test_a_json_api_request_sees_the_new_disk_state_too(app, tmp_path):
    client = app.test_client()
    wipe = {"provider": "openai"}

    write_settings(tmp_path, ENABLE_LLM_INFERENCE=False, LLM_PROVIDER="openai")
    quiet = client.post("/api/settings/wipe_token", json=wipe, headers=AUTH)
    assert quiet.status_code == 200
    assert quiet.get_json()["errors"] == {}, quiet.get_json()

    write_settings(tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai")

    loud = client.post("/api/settings/wipe_token", json=wipe, headers=AUTH)
    assert loud.status_code == 200
    assert loud.get_json()["errors"].get("ENV_TOKENS.openai") == {
        "type": "i18n", "value": "err_missing_token",
    }, "the endpoint answered from a remembered read of settings.json"
