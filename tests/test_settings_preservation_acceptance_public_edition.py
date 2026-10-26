# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_settings_preservation_acceptance import (  # noqa: F401 (fixture)
    AUTH, OPAQUE, exact, isolated, operation_headers, read, typed_view_keeps_exact_opaque_siblings, write,
)


def receipt_recovery_preserves_normalized_entry_after_provider_change(isolated):  # noqa: F811 (fixture)
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


@pytest.mark.parametrize("provider", [
    "openai", "claude", "gemini", "deepseek", "mistral", "zai", "ollama", "lm-studio",
])
def test_selected_preset_typed_view_keeps_exact_opaque_siblings(isolated, provider):  # noqa: F811 (fixture)
    typed_view_keeps_exact_opaque_siblings(isolated, provider)


@pytest.mark.parametrize("provider_shape", [{}, {"ollama": {}}, {"future": OPAQUE}])
def test_loader_runtime_defaults_never_materialize_missing_provider_fields(isolated, provider_shape):  # noqa: F811
    manager, _, raw = isolated
    raw.update(ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="ollama", LLM_PROVIDERS=provider_shape)
    write(manager, raw)
    for _ in range(2):
        loaded = manager.load_strict()
        assert loaded.ACTIVE_PROVIDER_CONFIG.url == "http://localhost:11434/v1/chat/completions"
        manager.save_settings(loaded)
        assert exact(read(manager)["LLM_PROVIDERS"]) == exact(provider_shape)


def test_receipt_recovery_preserves_normalized_entry_after_provider_change(isolated):  # noqa: F811 (fixture)
    receipt_recovery_preserves_normalized_entry_after_provider_change(isolated)


@pytest.mark.parametrize("omit_receipts", [False, True])
def test_replay_oracle_detects_missing_receipt_retention(isolated, monkeypatch, omit_receipts):  # noqa: F811
    if omit_receipts:
        monkeypatch.setattr(isolated[0].operations, "remember", lambda *args: None)
    if omit_receipts:
        with pytest.raises(AssertionError):
            receipt_recovery_preserves_normalized_entry_after_provider_change(isolated)
    else:
        receipt_recovery_preserves_normalized_entry_after_provider_change(isolated)
