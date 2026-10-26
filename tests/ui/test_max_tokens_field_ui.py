# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from fake_llm.harness import ready_ai

FIELD = 'input[name="LLM_PROVIDERS.custom.max_tokens_field"]'


def open_ai_tab(open_page):
    page = open_page(ready_ai())
    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    return page


def test_a_name_with_a_space_is_refused_on_the_visible_field(open_page):
    page = open_ai_tab(open_page)
    page.fill(FIELD, "max completion tokens")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    )

    state = page.evaluate(
        f"""() => {{
            const el = document.querySelector('{FIELD}');
            return {{
                value: el.value,
                visible: el.offsetParent !== null,
                highlighted: el.classList.contains('error-field'),
                message: document.getElementById('err-LLM_PROVIDERS.custom.max_tokens_field').textContent,
                errors: Object.keys(window.draftErrors || {{}}),
            }};
        }}"""
    )
    assert state["value"] == "max completion tokens", "the raw text survives the failed Apply"
    assert state["visible"] and state["highlighted"], "the field is on screen and highlighted"
    assert "without spaces" in state["message"], state["message"]
    assert state["errors"] == ["LLM_PROVIDERS.custom.max_tokens_field"], \
        "with the provider otherwise complete the ONLY error is the field name"


def test_label_and_hint_render_in_russian(open_page):
    page = open_ai_tab(open_page)
    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(300)

    texts = page.evaluate(
        """() => ({
            label: document.querySelector('#provider-custom [data-i18n="lbl_max_tokens_field"]').textContent,
            hint: document.querySelector('#provider-custom [data-i18n="hint_max_tokens_field"]').textContent,
            checkbox_hint: document.querySelector('#provider-custom [data-i18n="hint_req_max"]').textContent,
        })"""
    )
    assert texts["label"] == "Имя поля Max Tokens:"
    assert "max_completion_tokens" in texts["hint"]
    assert "Передавать параметр Max Tokens" in texts["hint"]
    assert page.locator('#provider-custom [data-i18n="hint_max_tokens_field"] a').get_attribute(
        "data-setting") == "LLM_PROVIDERS.custom.require_max_tokens"
    assert "Anthropic Claude" in texts["checkbox_hint"]
