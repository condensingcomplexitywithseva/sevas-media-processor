# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


AI_TOKEN_ERROR = {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai"}


def read_banner(page):
    return page.evaluate(
        """() => {
        const banner = document.getElementById('global-error-banner');
        const text = document.getElementById('global-error-text');
        const first = text && text.firstChild;
        const hint = document.getElementById('global-error-ai-hint');
        const warnGeneral = document.getElementById('warn-tab-general');
        const warnAi = document.getElementById('warn-tab-ai');
        return {
            banner_visible: !!(banner && getComputedStyle(banner).display !== 'none'),
            banner_is_column: banner ? getComputedStyle(banner).flexDirection : null,
            row_count: banner ? banner.querySelectorAll('.banner-row').length : 0,
            jump_btn_is_first_in_message: !!(first && first.nodeType === 1
                && first.classList.contains('banner-jump-btn')),
            jump_btn_text: first && first.nodeType === 1 ? first.textContent : null,
            jump_btn_margin_right: first && first.nodeType === 1
                ? getComputedStyle(first).marginRight : null,
            hint_visible: !!(hint && getComputedStyle(hint).display !== 'none'),
            hint_text: hint ? hint.textContent.trim() : null,
            warn_general_visible: !!(warnGeneral && getComputedStyle(warnGeneral).display !== 'none'),
            warn_ai_visible: !!(warnAi && getComputedStyle(warnAi).display !== 'none'),
        };
    }"""
    )


def test_banner_layout_with_ai_only_errors(open_page):
    page = open_page(AI_TOKEN_ERROR)
    state = read_banner(page)
    assert state["banner_visible"]
    assert state["banner_is_column"] == "column"
    assert state["row_count"] == 2
    assert state["jump_btn_is_first_in_message"], "jump button must precede the message text"
    assert state["jump_btn_text"] == "Go to error"
    assert state["jump_btn_margin_right"] == "8px", \
        "message jump button needs its own gap — the row gap does not reach inside the text span"
    assert state["hint_visible"], "AI escape-hatch hint must show for AI errors in AI mode"
    assert "Don't need AI?" in state["hint_text"]
    assert '"Turn media files to JPEGs"' in state["hint_text"], \
        "the hint must name the exact mode to switch to"
    assert not state["warn_general_visible"], "General tab must not be flagged for AI-only errors"
    assert state["warn_ai_visible"]


def test_goto_setting_navigates_without_touching_value(open_page):
    page = open_page(AI_TOKEN_ERROR)
    value_before = page.evaluate("document.getElementById('ENABLE_LLM_INFERENCE').value")
    page.click("#global-error-ai-hint .banner-jump-btn")
    page.wait_for_timeout(400)
    state = page.evaluate(
        """() => {
        const sel = document.getElementById('ENABLE_LLM_INFERENCE');
        const activeTab = document.querySelector('.sidebar-tab.active');
        return {
            active_tab: activeTab ? activeTab.getAttribute('data-tab') : null,
            group_pulsing: sel.closest('.form-group').classList.contains('error-flash'),
            select_value: sel.value,
            select_has_error_class: sel.classList.contains('error-field'),
        };
    }"""
    )
    assert state["active_tab"] == "general"
    assert state["group_pulsing"], "the mode form-group must pulse after [Go to setting]"
    assert state["select_value"] == value_before, "[Go to setting] must never change the value"
    assert not state["select_has_error_class"]


def test_jump_button_lands_on_ai_tab_error(open_page):
    page = open_page(AI_TOKEN_ERROR)
    page.click("#global-error-text .banner-jump-btn")
    page.wait_for_timeout(400)
    state = page.evaluate(
        """() => {
        const activeTab = document.querySelector('.sidebar-tab.active');
        const pane = document.getElementById('tab-content-ai');
        // The pulse lands on the broken field's form-group wrapper (the
        // input's own !important box-shadow styles would swallow it).
        const flashed = pane ? pane.querySelector('.error-flash') : null;
        const field = flashed ? flashed.querySelector('.error-field') : null;
        return {
            active_tab: activeTab ? activeTab.getAttribute('data-tab') : null,
            flashed_is_group: !!(flashed && flashed.classList.contains('form-group')),
            flashed_field: field ? (field.name || field.id || null) : null,
        };
    }"""
    )
    assert state["active_tab"] == "ai", "with AI-only errors the jump must land on the AI tab"
    assert state["flashed_is_group"], "the pulse must land on the form-group wrapper"
    assert state["flashed_field"] == "ENV_TOKENS.openai"


def test_ai_hint_impossible_when_mode_is_jpeg(open_page):
    page = open_page({"ENABLE_LLM_INFERENCE": False, "MAX_JPEGS_PER_INFERENCE": "abc"})
    state = read_banner(page)
    assert not state["banner_visible"], "AI off: a broken AI value must not banner"
    assert not state["warn_ai_visible"]
    assert not state["hint_visible"]


def test_no_banner_when_config_valid(open_page):
    page = open_page({})
    state = read_banner(page)
    assert not state["banner_visible"]
    assert not state["warn_general_visible"]
    assert not state["warn_ai_visible"]


def test_banner_strings_render_in_russian(open_page):
    page = open_page(AI_TOKEN_ERROR)
    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(300)
    state = read_banner(page)
    assert state["jump_btn_text"] == "К ошибке"
    assert "Не нужен ИИ?" in state["hint_text"]
    assert "«Превращаем файлы в JPEG»" in state["hint_text"], \
        "the RU hint must name the exact mode to switch to"
    assert "К настройке" in state["hint_text"]
