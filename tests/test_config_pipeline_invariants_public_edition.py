# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_config_pipeline_invariants import base_paths, client, mgr  # noqa: F401 (fixtures)


def test_business_errors_identical_for_ai_rules_too(mgr, tmp_path):  # noqa: F811 (fixture)
    payload = base_paths(tmp_path, ENABLE_LLM_INFERENCE=True, LLM_PROVIDER="openai")
    _, errors_types_ok, _ = mgr.validate_draft(payload)
    _, errors_types_bad, _ = mgr.validate_draft({**payload, "MAX_DIMENSION": "abc"})

    assert errors_types_ok["ENV_TOKENS.openai"] == errors_types_bad["ENV_TOKENS.openai"]
    assert set(errors_types_bad) == set(errors_types_ok) | {"MAX_DIMENSION"}


def test_error_catalogue_missing_token_shape(mgr, tmp_path):  # noqa: F811 (fixture)
    ai = {"ENABLE_LLM_INFERENCE": True,
          "ENV_TOKENS": {"openai": "sk-fake0123456789abcdef0123456789abcdef"}}
    overrides, field, expected = (
        {**ai, "LLM_PROVIDER": "openai", "ENV_TOKENS": {}}, "ENV_TOKENS.openai",
        {"type": "i18n", "value": "err_missing_token"})
    _, errors, _ = mgr.validate_draft(base_paths(tmp_path, **overrides))
    assert errors.get(field) == expected, (overrides, field, errors)


def test_commit_new_token_lands_in_env_file(client, mgr, tmp_path):  # noqa: F811 (fixture)
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
def test_commit_masked_or_empty_token_changes_nothing(client, mgr, tmp_path, echoed_value):  # noqa: F811
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


def test_wipe_endpoint_deletes_immediately_and_reports_errors(client, mgr, tmp_path):  # noqa: F811 (fixture)
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
