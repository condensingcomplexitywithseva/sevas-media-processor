# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


AI_TOKEN_ERROR = {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai"}


def test_jump_button_lands_on_the_missing_token(open_page):
    page = open_page(AI_TOKEN_ERROR)
    assert list(page.evaluate("Object.keys(window.diskErrors)")) == ["ENV_TOKENS.openai"]
    page.click("#global-error-text .banner-jump-btn")
    page.wait_for_timeout(400)
    state = page.evaluate(
        """() => {
        const activeTab = document.querySelector('.sidebar-tab.active');
        const pane = document.getElementById('tab-content-ai');
        const flashed = pane ? pane.querySelector('.error-flash') : null;
        const field = flashed ? flashed.querySelector('.error-field') : null;
        return {
            active_tab: activeTab ? activeTab.getAttribute('data-tab') : null,
            flashed_is_group: !!(flashed && flashed.classList.contains('form-group')),
            flashed_field: field ? (field.name || field.id || null) : null,
        };
    }"""
    )
    assert state["active_tab"] == "ai"
    assert state["flashed_is_group"]
    assert state["flashed_field"] == "ENV_TOKENS.openai"
