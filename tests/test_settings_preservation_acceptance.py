# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import os
import sys
from pathlib import Path
import threading

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import config_loader
from config_loader import ConfigManager, TokenManager
from config_validator import AI_TAB_FIELDS
from routes.web_server import SESSION_TOKEN, create_app

AUTH = {"X-App-Token": SESSION_TOKEN}
AI_FIELDS = (
    "LLM_PROVIDER", "LLM_PROVIDERS", "LLM_USER_PROMPT", "LLM_USER_PROMPT_MODE",
    "LLM_SYSTEM_PROMPT", "LLM_SYSTEM_PROMPT_MODE", "LLM_OUTPUT_MODE",
    "LLM_OUTPUT_COLUMNS", "LLM_OUTPUT_COLUMN_INSTRUCTIONS", "LLM_JSON_MAX_ATTEMPTS",
    "LLM_ABORT_ON_MALFORMED_JSON", "MAX_JPEGS_PER_INFERENCE",
    "MAX_CONSECUTIVE_LLM_FAILURES", "HALT_ON_LLM_PARSE_ERROR", "LLM_MAX_RETRIES",
    "LLM_TIMEOUT_SECONDS", "LLM_RETRY_SLEEP_SECONDS",
)
HOSTILE = [None, [], {"a.b": {}, "values": [False, 0, 9007199254740993]},
           "false", 9007199254740993]
OPAQUE = {"": {}, "a.b": [None, False, 0, 9007199254740993],
          "hasOwnProperty": {"__proto__": []}, "text": "<b>literal</b> Привет"}


def exact(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name in list(os.environ):
        if name.endswith("_TOKEN"):
            monkeypatch.delenv(name)
    manager = ConfigManager(tmp_path)
    manager.app_data_dir = tmp_path
    manager.env_path = tmp_path / ".env"
    manager.token_manager = TokenManager(manager.env_path)
    monkeypatch.setattr(config_loader, "_manager", manager)
    (tmp_path / "input").mkdir()
    raw = {"INPUT_FOLDER_PATH": str(tmp_path / "input"),
           "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
           "ENABLE_LLM_INFERENCE": False, "future.key": copy.deepcopy(OPAQUE)}
    return manager, create_app().test_client(), raw


def write(manager, raw):
    manager.settings_path.write_text(json.dumps(raw), encoding="utf-8")


def read(manager):
    return json.loads(manager.settings_path.read_text(encoding="utf-8"))


def test_dormancy_matrix_names_every_non_token_ai_field():
    assert set(AI_FIELDS) == AI_TAB_FIELDS - {"ENV_TOKENS"}


@pytest.mark.parametrize("field", AI_FIELDS)
@pytest.mark.parametrize("value", HOSTILE)
def test_every_dormant_field_survives_reads_and_repeated_edit_saves(isolated, field, value):
    manager, client, raw = isolated
    raw[field] = copy.deepcopy(value)
    write(manager, raw)
    before = manager.settings_path.read_bytes()
    assert manager.load_for_ui()[1] == {}
    manager.load_strict()
    assert client.get("/").status_code == 200
    assert manager.settings_path.read_bytes() == before
    for quality in (81, 82):
        response = client.post("/api/settings/commit", json={"edits": {"JPEG_QUALITY": quality}}, headers=AUTH)
        assert response.status_code == 200, response.get_json()
        saved = read(manager)
        assert saved["JPEG_QUALITY"] == quality
        assert exact({key: saved[key] for key in AI_FIELDS if key in saved}) == exact({field: value})
        assert exact(saved["future.key"]) == exact(OPAQUE)
        assert "ENV_TOKENS" not in saved
        assert not manager.env_path.exists()


@pytest.mark.parametrize("provider", [
    "openai", "claude", "gemini", "deepseek", "mistral", "ollama", "lm-studio", "custom",
])
def test_selected_provider_typed_view_keeps_exact_opaque_siblings(isolated, provider):
    manager, client, raw = isolated
    manager.token_manager.update_tokens({provider: "fake-token-for-validation-only"})
    raw.update(ENABLE_LLM_INFERENCE=True, LLM_PROVIDER=provider,
               LLM_PROVIDERS={provider: {"url": "http://127.0.0.1:9/no-request", "model": "probe",
                                        "max_tokens": "00123", "require_max_tokens": "false", "future": OPAQUE},
                              "future.provider": OPAQUE, "": [False, {}]})
    write(manager, raw)
    before = manager.settings_path.read_bytes()
    loaded = manager.load_strict()
    assert loaded.ACTIVE_PROVIDER_CONFIG.max_tokens == 123
    assert loaded.ACTIVE_PROVIDER_CONFIG.require_max_tokens is False
    assert manager.settings_path.read_bytes() == before
    response = client.post("/api/settings/commit", json={"edits": {"JPEG_QUALITY": 81}}, headers=AUTH)
    assert response.status_code == 200, response.get_json()
    expected = copy.deepcopy(raw["LLM_PROVIDERS"])
    expected[provider].update(max_tokens=123, require_max_tokens=False)
    assert exact(read(manager)["LLM_PROVIDERS"]) == exact(expected)


@pytest.mark.parametrize("bad", [None, [], {}, "abc", 0, -1])
def test_active_invalid_edit_refuses_all_settings_and_token_writes(isolated, bad):
    manager, client, raw = isolated
    raw.update(ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="ollama")
    write(manager, raw)
    manager.token_manager.update_tokens({"openai": "fake-original-token"})
    before = manager.settings_path.read_bytes(), manager.env_path.read_bytes()
    response = client.post("/api/settings/commit", headers=AUTH, json={"edits": {
        "JPEG_QUALITY": 81, "LLM_PROVIDERS.ollama.max_tokens": bad,
        "ENV_TOKENS.openai": "fake-replacement-token"}})
    assert response.status_code == 400, response.get_json()
    assert "LLM_PROVIDERS.ollama.max_tokens" in response.get_json()["errors"]
    assert (manager.settings_path.read_bytes(), manager.env_path.read_bytes()) == before


@pytest.mark.parametrize("body", [None, [], ["JPEG_QUALITY", 81], "invalid", 42, True])
def test_non_object_commit_is_a_client_error_without_writes(isolated, body):
    manager, client, raw = isolated
    write(manager, raw)
    before = manager.settings_path.read_bytes()
    response = client.post("/api/settings/commit", data=json.dumps(body), content_type="application/json", headers=AUTH)
    assert manager.settings_path.read_bytes() == before
    assert not manager.env_path.exists()
    assert response.status_code == 400, response.get_json()


@pytest.mark.parametrize("switch", ["not-a-bool", None, [], {}])
def test_invalid_enable_switch_never_bypasses_validation(isolated, switch):
    manager, client, raw = isolated
    write(manager, raw)
    before = manager.settings_path.read_bytes()
    response = client.post("/api/settings/commit", headers=AUTH,
                           json={"edits": {"ENABLE_LLM_INFERENCE": switch, "JPEG_QUALITY": 81}})
    assert response.status_code == 400
    assert "ENABLE_LLM_INFERENCE" in response.get_json()["errors"]
    assert manager.settings_path.read_bytes() == before


def test_edit_candidate_and_validation_do_not_alias_caller_or_file(isolated):
    manager, _, raw = isolated
    write(manager, raw)
    edits = {"LLM_USER_PROMPT": copy.deepcopy(OPAQUE)}
    expected = exact(edits)
    before = manager.settings_path.read_bytes()
    candidate = manager.settings_with_edits(edits)
    obj, errors, merged = manager.validate_draft(candidate)
    assert obj is not None and not errors
    candidate["LLM_USER_PROMPT"]["a.b"].append("candidate-change")
    merged["future.key"]["a.b"].append("view-change")
    assert exact(edits) == expected
    assert manager.settings_path.read_bytes() == before


def test_explicit_leaf_repair_preserves_opaque_provider_neighbors(isolated):
    manager, client, raw = isolated
    raw["LLM_PROVIDERS"] = {"ollama": {"model": None, "future": OPAQUE}, "claude": [False, {}]}
    write(manager, raw)
    response = client.post("/api/settings/commit", headers=AUTH,
                           json={"edits": {"LLM_PROVIDERS.ollama.model": "repaired"}})
    assert response.status_code == 200
    expected = {"ollama": {"model": "repaired", "future": OPAQUE}, "claude": [False, {}]}
    assert exact(read(manager)["LLM_PROVIDERS"]) == exact(expected)


@pytest.mark.parametrize("provider_shape", [{}, {"ollama": {}}, {"future": OPAQUE}])
def test_loader_runtime_defaults_never_materialize_missing_provider_fields(isolated, provider_shape):
    manager, _, raw = isolated
    raw.update(ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="ollama", LLM_PROVIDERS=provider_shape)
    write(manager, raw)
    for _ in range(2):
        loaded = manager.load_strict()
        assert loaded.ACTIVE_PROVIDER_CONFIG.url == "http://localhost:11434/v1/chat/completions"
        manager.save_settings(loaded)
        assert exact(read(manager)["LLM_PROVIDERS"]) == exact(provider_shape)


@pytest.mark.parametrize("enabled", [False, True])
def test_nested_full_draft_preserves_opaque_shapes(isolated, enabled):
    manager, client, raw = isolated
    raw.update(ENABLE_LLM_INFERENCE=enabled, LLM_PROVIDER="ollama",
               LLM_PROVIDERS={"ollama": {"model": "probe", "future": OPAQUE}, "future.provider": OPAQUE})
    raw["future"] = raw.pop("future.key")
    write(manager, raw)
    response = client.post("/api/settings/commit", headers=AUTH, json={**raw, "JPEG_QUALITY": 81})
    assert response.status_code == 200
    assert exact(read(manager)["LLM_PROVIDERS"]) == exact(raw["LLM_PROVIDERS"])
    assert exact(read(manager)["future"]) == exact(OPAQUE)


@pytest.mark.parametrize("serialized_control", [False, True], ids=["production", "serialized-control"])
def test_disjoint_overlapping_control_edits_both_survive(isolated, monkeypatch, serialized_control):
    import routes.settings_api as api

    manager, _, raw = isolated
    write(manager, raw)
    original = api.validate_draft
    first_validated = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    transaction = threading.Lock()

    def delay_first(payload):
        result = original(payload)
        if payload.get("JPEG_QUALITY") == 81:
            first_validated.set()
            assert release_first.wait(timeout=10), "test coordinator did not release the first request"
        return result

    monkeypatch.setattr(api, "validate_draft", delay_first)

    def commit(edits):
        guard = transaction if serialized_control else nullcontext()
        with guard, create_app().test_client() as client:
            response = client.post("/api/settings/commit", headers=AUTH, json={"edits": edits})
            if "MAX_DIMENSION" in edits:
                second_finished.set()
            return response.status_code, response.get_json()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(commit, {"JPEG_QUALITY": 81})
        try:
            assert first_validated.wait(timeout=5)
            second = executor.submit(commit, {"MAX_DIMENSION": 1234})
            second_finished.wait(timeout=1)
        finally:
            release_first.set()
        outcomes = [first.result(timeout=10), second.result(timeout=10)]
    assert [code for code, _ in outcomes] == [200, 200], outcomes
    result = read(manager)
    assert (result["JPEG_QUALITY"], result["MAX_DIMENSION"]) == (81, 1234), (
        "successful disjoint edits must not overwrite each other")
    assert exact(result["future.key"]) == exact(OPAQUE)



@pytest.mark.parametrize("mode,expected", [("broken-json", 400), ("wrong-media-type", 415), ("valid", 200)])
def test_request_protocol_errors_preserve_their_status(isolated, mode, expected):
    manager, client, raw = isolated
    write(manager, raw)
    manager.token_manager.update_tokens({"openai": "fake-original-probe-token"})
    before = manager.settings_path.read_bytes(), manager.env_path.read_bytes()
    body = "{broken" if mode == "broken-json" else json.dumps(raw)
    content_type = "text/plain" if mode == "wrong-media-type" else "application/json"
    response = client.post("/api/settings/commit", data=body, content_type=content_type, headers=AUTH)
    if expected != 200:
        assert (manager.settings_path.read_bytes(), manager.env_path.read_bytes()) == before
    assert response.status_code == expected


@pytest.mark.parametrize('omit_lock', [False, True])
def test_original_lost_update_assertion_still_detects_missing_transaction(isolated, monkeypatch, omit_lock):
    from contextlib import nullcontext

    if omit_lock:
        monkeypatch.setattr(isolated[0], '_lock', nullcontext())
    def check():
        test_disjoint_overlapping_control_edits_both_survive(isolated, monkeypatch, False)
    if omit_lock:
        with pytest.raises(AssertionError, match='successful disjoint edits'):
            check()
    else:
        check()


def operation_headers(manager, *, base=None, identity=None):
    import uuid

    return dict(
        AUTH,
        **{
            "X-Settings-Operation": identity or str(uuid.uuid4()),
            "X-Settings-Epoch": manager.operations.epoch,
            "X-Settings-Revision": str(manager.operations.revision if base is None else base),
        },
    )


def test_receipt_recovery_preserves_normalized_entry_after_provider_change(isolated):
    manager, client, raw = isolated
    raw.update(
        ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="ollama", LLM_PROVIDERS={"ollama": {"max_tokens": 8, "future": OPAQUE}}
    )
    write(manager, raw)
    headers = operation_headers(manager)
    payload = {"edits": {"LLM_PROVIDERS.ollama.max_tokens": "9"}}
    first = client.post("/api/settings/commit", headers=headers, json=payload)
    assert first.status_code == 200
    expected = {"max_tokens": 9, "future": OPAQUE}
    assert exact(read(manager)["LLM_PROVIDERS"]["ollama"]) == exact(expected)
    changed = client.post("/api/settings/commit", headers=AUTH, json={"edits": {"LLM_PROVIDER": "lm-studio"}})
    assert changed.status_code == 200
    before = manager.settings_path.read_bytes()
    replay = client.post("/api/settings/commit", headers=headers, json=payload)
    assert replay.status_code == 200
    assert manager.settings_path.read_bytes() == before
    assert exact(read(manager)["LLM_PROVIDERS"]["ollama"]) == exact(expected)
    data = replay.get_json()
    assert data["operation_revision"] == first.get_json()["operation_revision"]
    assert data["snapshot"]["revision"] == changed.get_json()["snapshot"]["revision"]
    assert data["snapshot"]["settings"]["LLM_PROVIDER"] == "lm-studio"


@pytest.mark.parametrize("invalid", [False, True], ids=["saved", "rejected"])
def test_receipts_do_not_repeat_writes_or_revalidate_old_intent(isolated, monkeypatch, invalid):
    import routes.settings_api as api

    manager, client, raw = isolated
    write(manager, raw)
    headers = operation_headers(manager)
    payload = {"edits": {"JPEG_QUALITY": "500" if invalid else "81"}}
    first = client.post("/api/settings/commit", headers=headers, json=payload)
    before = manager.settings_path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Recovered operation must not validate or save the submitted payload again")

    monkeypatch.setattr(api, "validate_draft", forbidden)
    monkeypatch.setattr(api, "save_settings", forbidden)
    second = client.post("/api/settings/commit", headers=headers, json=payload)
    assert first.status_code == second.status_code == (400 if invalid else 200)
    assert second.get_json()["operation_revision"] == first.get_json()["operation_revision"]
    assert manager.settings_path.read_bytes() == before


@pytest.mark.parametrize(
    "fault", ["epoch", "future", "negative", "identity", "different-body", "different-route", "expired"]
)
def test_unrecognized_receipts_never_become_new_writes(isolated, fault):
    manager, client, raw = isolated
    write(manager, raw)
    headers = operation_headers(manager)
    payload = {"edits": {"JPEG_QUALITY": "81"}}
    assert client.post("/api/settings/commit", headers=headers, json=payload).status_code == 200
    route = "/api/settings/commit"
    if fault == "epoch":
        headers["X-Settings-Epoch"] = "another-process"
    if fault == "future":
        headers["X-Settings-Revision"] = "999999"
    if fault == "negative":
        headers["X-Settings-Revision"] = "-1"
    if fault == "identity":
        headers["X-Settings-Operation"] = "invalid"
    if fault == "different-body":
        payload = {"edits": {"JPEG_QUALITY": "82"}}
    if fault == "different-route":
        route, payload = "/api/settings/reset", {}
    if fault == "expired":
        for _ in range(manager.operations.RETAINED_RECEIPTS):
            response = client.post("/api/settings/commit", headers=operation_headers(manager), json={"edits": {}})
            assert response.status_code == 200
        assert len(manager.operations.receipts) == manager.operations.RETAINED_RECEIPTS
    before = manager.settings_path.read_bytes()
    response = client.post(route, headers=headers, json=payload)
    assert response.status_code == 409
    assert response.get_json()["status"] == "fatal"
    assert manager.settings_path.read_bytes() == before
    assert not list(manager.root_dir.glob("settings_corrupted_backup_*"))


@pytest.mark.parametrize("delayed_arrival", [False, True], ids=["saved-before-wipe", "arrives-after-wipe"])
def test_wipe_supersedes_older_token_submission_without_resurrection(isolated, delayed_arrival):
    manager, client, raw = isolated
    write(manager, raw)
    headers = operation_headers(manager)
    token = "fake-sensitive-operation-token"
    payload = {"edits": {"ENV_TOKENS.openai": token}}
    if not delayed_arrival:
        assert client.post("/api/settings/commit", headers=headers, json=payload).status_code == 200
    wiped = client.post("/api/settings/wipe_token", headers=operation_headers(manager), json={"provider": "openai"})
    assert wiped.status_code == 200
    result = client.post("/api/settings/commit", headers=headers, json=payload)
    assert result.status_code == (409 if delayed_arrival else 200)
    assert result.get_json()["status"] == ("superseded" if delayed_arrival else "success")
    assert not manager.get_env_tokens()
    assert result.get_json()["snapshot"]["env_tokens"] == {}
    assert token not in json.dumps(manager.operations.receipts)
    assert token not in result.get_data(as_text=True)
    assert "ENV_TOKENS" not in read(manager)
    assert (
        client.post(
            "/api/settings/commit", headers=operation_headers(manager), json={"edits": {"JPEG_QUALITY": 82}}
        ).status_code
        == 200
    )
    assert client.post("/api/settings/commit", headers=headers, json=payload).status_code == result.status_code
    assert not manager.get_env_tokens()


@pytest.mark.parametrize("operation", ["reset", "wipe_token"])
def test_other_settings_operations_have_ordered_safe_receipts(isolated, operation):
    manager, client, raw = isolated
    write(manager, raw)
    manager.token_manager.update_tokens({"openai": "fake-original"})
    headers = operation_headers(manager)
    payload = {"provider": "openai"} if operation == "wipe_token" else {}
    route = "/api/settings/" + operation
    first = client.post(route, headers=headers, json=payload)
    assert first.status_code == 200
    before = manager.settings_path.read_bytes(), manager.env_path.read_bytes()
    second = client.post(route, headers=headers, json=payload)
    assert second.status_code == 200
    assert second.get_json()["snapshot"] == first.get_json()["snapshot"]
    assert (manager.settings_path.read_bytes(), manager.env_path.read_bytes()) == before
    assert len(list(manager.root_dir.glob("settings_corrupted_backup_*"))) == (1 if operation == "reset" else 0)


def test_partial_save_failure_is_retained_as_unknown_without_repeating_writes(isolated, monkeypatch):
    import routes.settings_api as api

    manager, client, raw = isolated
    write(manager, raw)

    def fail_tokens(*args, **kwargs):
        raise OSError("isolated token write refused")

    monkeypatch.setattr(api, "update_env_tokens", fail_tokens)
    headers = operation_headers(manager)
    payload = {"edits": {"JPEG_QUALITY": 81, "ENV_TOKENS.openai": "fake-sensitive"}}
    response = client.post("/api/settings/commit", headers=headers, json=payload)
    assert response.status_code == 500 and read(manager)["JPEG_QUALITY"] == 81

    def forbidden(*args, **kwargs):
        pytest.fail("A partial operation must not be executed again")

    monkeypatch.setattr(api, "save_settings", forbidden)
    replay = client.post("/api/settings/commit", headers=headers, json=payload)
    assert replay.status_code == 500 and replay.get_json()["status"] == "fatal"
    assert not manager.get_env_tokens()


@pytest.mark.parametrize("omit_receipts", [False, True])
def test_replay_oracle_detects_missing_receipt_retention(isolated, monkeypatch, omit_receipts):
    if omit_receipts:
        monkeypatch.setattr(isolated[0].operations, "remember", lambda *args: None)
    if omit_receipts:
        with pytest.raises(AssertionError):
            test_receipt_recovery_preserves_normalized_entry_after_provider_change(isolated)
    else:
        test_receipt_recovery_preserves_normalized_entry_after_provider_change(isolated)
