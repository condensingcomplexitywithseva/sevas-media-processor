# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from config_validator import IDENTITY_SETTINGS
from test_run_identity_classification import IDENTITY_URL, PUBLIC_EDITION_CASES, _identity


def test_the_shared_file_leaves_only_the_provider_choice_here():
    assert {"LLM_PROVIDER"} == PUBLIC_EDITION_CASES


def test_choosing_another_provider_changes_the_run_identity(tmp_path):
    assert "LLM_PROVIDER" in IDENTITY_SETTINGS
    other = {"LLM_PROVIDER": "openai", "LLM_PROVIDERS": {"openai": {"url": IDENTITY_URL}}}
    assert _identity(tmp_path, **other) != _identity(tmp_path)


def test_a_presets_own_values_typed_explicitly_and_inactive_presets_keep_the_identity(tmp_path):
    from config_validator import _default_provider_configs
    openai = {"LLM_PROVIDER": "openai", "LLM_PROVIDERS": {"openai": {"url": IDENTITY_URL}}}
    explicit = {"LLM_PROVIDER": "openai", "LLM_PROVIDERS": {"openai": {
        "url": IDENTITY_URL, "model": _default_provider_configs()["openai"].model}}}
    inactive = {"LLM_PROVIDER": "openai", "LLM_PROVIDERS": {
        "openai": {"url": IDENTITY_URL}, "claude": {"model": "other-model"}}}
    assert _identity(tmp_path, **explicit) == _identity(tmp_path, **openai)
    assert _identity(tmp_path, **inactive) == _identity(tmp_path, **openai)
    changed = {"LLM_PROVIDER": "openai", "LLM_PROVIDERS": {"openai": {"url": IDENTITY_URL, "model": "x"}}}
    assert _identity(tmp_path, **changed) != _identity(tmp_path, **openai), "control"
