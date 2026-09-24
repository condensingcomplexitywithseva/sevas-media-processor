# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_validator import _default_provider_configs

FAKE_OPENAI_TOKEN = "sk-fake0123456789abcdef0123456789abcdef"
AI_ON_OPENAI = {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai"}


def field(provider):
    return f'input[name="LLM_PROVIDERS.{provider}.max_tokens_field"]'


def open_ai_tab(open_page):
    page = open_page(AI_ON_OPENAI, tokens={"openai": FAKE_OPENAI_TOKEN})
    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    return page


def test_every_provider_frame_carries_its_presets_field_name(open_page):
    page = open_ai_tab(open_page)
    presets = _default_provider_configs()

    values = page.evaluate(
        "(names) => Object.fromEntries(names.map(n => [n, document.querySelector(n).value]))",
        [field(p) for p in presets],
    )

    assert values == {field(p): cfg.max_tokens_field for p, cfg in presets.items()}
    assert values[field("openai")] == "max_completion_tokens"
    assert values[field("claude")] == "max_tokens"


def test_a_name_with_a_space_is_refused_on_the_visible_field(open_page):
    page = open_ai_tab(open_page)
    page.fill(field("openai"), "max completion tokens")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    )

    state = page.evaluate(
        f"""() => {{
            const el = document.querySelector('{field("openai")}');
            return {{
                value: el.value,
                visible: el.offsetParent !== null,
                highlighted: el.classList.contains('error-field'),
                message: document.getElementById('err-LLM_PROVIDERS.openai.max_tokens_field').textContent,
                errors: Object.keys(window.draftErrors || {{}}),
            }};
        }}"""
    )
    assert state["value"] == "max completion tokens", "the raw text survives the failed Apply"
    assert state["visible"] and state["highlighted"], "the field is on screen and highlighted"
    assert "without spaces" in state["message"], state["message"]
    assert state["errors"] == ["LLM_PROVIDERS.openai.max_tokens_field"], \
        "with a token present the ONLY error is the field name"


def test_label_and_hint_render_in_russian(open_page):
    page = open_ai_tab(open_page)
    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(300)

    texts = page.evaluate(
        """() => ({
            label: document.querySelector('#provider-openai [data-i18n="lbl_max_tokens_field"]').textContent,
            hint: document.querySelector('#provider-openai [data-i18n="hint_max_tokens_field"]').textContent,
            checkbox_hint: document.querySelector('#provider-openai [data-i18n="hint_req_max"]').textContent,
        })"""
    )
    assert texts["label"] == "Имя поля Max Tokens:"
    assert "max_completion_tokens" in texts["hint"]
    assert "Передавать параметр Max Tokens" in texts["hint"]
    assert page.locator('#provider-openai [data-i18n="hint_max_tokens_field"] a').get_attribute(
        "data-setting") == "LLM_PROVIDERS.openai.require_max_tokens"
    assert "Anthropic Claude" in texts["checkbox_hint"]


def test_local_server_context_hint_only_in_the_local_frames(open_page):
    page = open_ai_tab(open_page)

    frames = page.evaluate(
        """() => Array.from(document.querySelectorAll('[data-i18n="hint_local_context"]'))
                     .map(el => el.closest('[id^="provider-"]').id)"""
    )

    assert sorted(frames) == ["provider-lm-studio", "provider-ollama"]
