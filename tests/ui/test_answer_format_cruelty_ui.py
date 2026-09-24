# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
APP_WIDTH = 1264
APP_HEIGHT = 800

DORMANT_GARBAGE = {
    "LLM_OUTPUT_COLUMNS": "page",
    "LLM_JSON_MAX_ATTEMPTS": "lots",
}
AI_OFF_LOCAL = {"ENABLE_LLM_INFERENCE": False, "LLM_PROVIDER": "ollama"}


def banner_visible(page):
    return page.evaluate(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'")


def start_enabled(page):
    return page.evaluate("!document.getElementById('btn-start').disabled")


def field_values(page, names):
    return page.evaluate(
        "(names) => Object.fromEntries(names.map(n => [n, document.getElementById(n).value]))",
        names)


def test_new_ai_fields_are_dormant_in_the_window_until_ai_is_on(open_page):
    page = open_page(AI_OFF_LOCAL | DORMANT_GARBAGE)
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    page.wait_for_timeout(200)

    assert not banner_visible(page), "dormant garbage must raise no banner"
    assert start_enabled(page), "an AI-off run must be allowed"
    assert field_values(page, list(DORMANT_GARBAGE)) == DORMANT_GARBAGE

    page.evaluate("window.switchTab('general')")
    page.wait_for_timeout(300)
    page.select_option("#ENABLE_LLM_INFERENCE", "true")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_selector("#global-error-text .banner-jump-btn", timeout=5000)

    errors = set(page.evaluate("Object.keys(window.draftErrors)"))
    assert set(DORMANT_GARBAGE) <= errors, errors
    assert not start_enabled(page), "Start must be refused while errors stand"

    page.click("#global-error-text .banner-jump-btn")
    page.wait_for_timeout(400)
    active = page.evaluate(
        "document.querySelector('.sidebar-tab.active').getAttribute('data-tab')")
    assert active == "ai"
    assert field_values(page, list(DORMANT_GARBAGE)) == DORMANT_GARBAGE, \
        "navigation must never mutate a value"


def test_a_dormant_value_the_select_cannot_show_is_not_rewritten(open_page, tmp_path):
    page = open_page(AI_OFF_LOCAL | {"LLM_OUTPUT_MODE": "raw_text"})
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    page.wait_for_timeout(200)
    assert not banner_visible(page)

    page.evaluate("window.switchTab('general')")
    page.wait_for_timeout(300)
    page.select_option("#ENABLE_LLM_INFERENCE", "true")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_timeout(800)

    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["LLM_OUTPUT_MODE"] == "raw_text", \
        "Apply rewrote a value the user never typed"
    assert on_disk["ENABLE_LLM_INFERENCE"] is False, "Apply is all-or-nothing"
    assert "LLM_OUTPUT_MODE" in page.evaluate("Object.keys(window.draftErrors)")


def discovered_locales():
    return sorted(p.stem for p in (SRC / "locales").glob("*.json"))


def test_the_two_stop_toggles_are_distinguishable_in_every_locale(open_page):
    page = open_page({"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "ollama"})
    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)

    locales = discovered_locales()
    assert locales, "no locale files discovered"
    for locale in locales:
        page.evaluate(f"changeLanguage('{locale}')")
        page.wait_for_timeout(300)
        state = page.evaluate("""() => {
            const text = (key) => document.querySelector(`[data-i18n="${key}"]`).textContent.trim();
            const group = (id) => document.getElementById(id).closest('.form-group');
            const parse = group('HALT_ON_LLM_PARSE_ERROR');
            const abort = group('LLM_ABORT_ON_MALFORMED_JSON');
            let hops = 0, node = parse;
            while (node && node !== abort && hops < 3) { node = node.nextElementSibling; hops += 1; }
            const wrapper = document.getElementById('ai-settings-wrapper');
            const jsonCarriers = Array.from(wrapper.querySelectorAll('[data-i18n]'))
                .filter(el => el.textContent.includes('JSON'))
                .map(el => el.getAttribute('data-i18n'));
            return {
                parse_label: text('lbl_llm_parse_err'),
                abort_label: text('lbl_llm_abort_malformed'),
                parse_hint: text('hint_llm_parse_err'),
                abort_hint: text('hint_llm_abort_malformed'),
                adjacent: node === abort,
                json_carriers: jsonCarriers,
            };
        }""")
        assert state["parse_label"] != state["abort_label"], locale
        assert state["parse_hint"] != state["abort_hint"], locale
        assert state["parse_label"] and state["abort_label"], locale
        assert state["adjacent"], (locale, "the two toggles are not neighbours")
        assert set(state["json_carriers"]) <= {"hint_llm_abort_malformed",
                                               "hint_extract_path"}, \
            (locale, state["json_carriers"])
