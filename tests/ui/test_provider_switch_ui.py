# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import json

CLAUDE_MAX_TOKENS = 'input[name="LLM_PROVIDERS.claude.max_tokens"]'

FAKE_TOKENS = {
    "openai": "sk-fake0123456789abcdef0123456789abcdef",
    "claude": "sk-ant-fake0123456789abcdefABCDEF",
    "gemini": "AIzaFAKE0123456789abcdefghijklmnopqrst",
    "mistral": "fake-mistral-token-not-a-real-key",
}

BROKEN_THREE = {
    "claude": {"max_tokens": "abc"},
    "gemini": {"max_tokens": "abc"},
    "mistral": {"max_tokens": "abc"},
}


def open_ai_tab(page):
    page.click('[data-tab="ai"]')
    page.wait_for_timeout(400)


def modal_visible(page):
    return page.evaluate(
        "getComputedStyle(document.getElementById('modal-overlay')).display !== 'none'"
    )


def banner_and_errors(page):
    return page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            errors: Object.keys(window.draftErrors || {}),
            apply_disabled: document.getElementById('btn-apply').disabled,
        })"""
    )


def test_disk_garbage_in_non_selected_provider_shows_no_error(open_page):
    page = open_page(
        {
            "ENABLE_LLM_INFERENCE": True,
            "LLM_PROVIDER": "openai",
            "LLM_PROVIDERS": {"claude": {"max_tokens": "abc"}},
        },
        tokens=FAKE_TOKENS,
    )
    state = page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            disk_errors: Object.keys(window.diskErrors || {}),
            start_disabled: document.getElementById('btn-start').disabled,
        })"""
    )
    assert not state["banner_visible"], "no banner for a non-selected provider's value"
    assert state["disk_errors"] == []
    assert not state["start_disabled"], "Start must not be blocked"


def test_ai_off_means_no_ai_errors_even_for_selected_provider(open_page):
    page = open_page(
        {
            "ENABLE_LLM_INFERENCE": False,
            "LLM_PROVIDER": "claude",
            "LLM_PROVIDERS": {"claude": {"max_tokens": "abc"}},
        }
    )
    state = page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            disk_errors: Object.keys(window.diskErrors || {}),
            start_disabled: document.getElementById('btn-start').disabled,
        })"""
    )
    assert not state["banner_visible"], "AI is off — nothing AI-related may block"
    assert state["disk_errors"] == []
    assert not state["start_disabled"]


def test_garbage_in_selected_provider_still_shows_visible_error(open_page):
    page = open_page(
        {
            "ENABLE_LLM_INFERENCE": True,
            "LLM_PROVIDER": "claude",
            "LLM_PROVIDERS": {"claude": {"max_tokens": "abc"}},
        },
        tokens=FAKE_TOKENS,
    )
    state = banner_and_errors(page)
    assert state["banner_visible"], "selected provider's broken value must show the banner"
    assert state["errors"] == ["LLM_PROVIDERS.claude.max_tokens"], \
        "with a token present the ONLY error is the structural one"
    page.click("#global-error-text .banner-jump-btn")
    page.wait_for_timeout(600)
    state = page.evaluate(
        f"""() => {{
        const el = document.querySelector('{CLAUDE_MAX_TOKENS}');
        return {{
            visible: el.offsetParent !== null,
            // The pulse lands on the form-group wrapper — the input's own
            // !important box-shadow styles would render it invisible there
            // (see test_error_navigation_ui.py).
            flashed: el.closest('.form-group').classList.contains('error-flash'),
        }};
    }}"""
    )
    assert state["visible"] and state["flashed"], "jump must land on the visible broken field"


def test_switch_away_with_unsaved_edits_asks_and_cancel_stays(open_page):
    page = open_page(
        {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "claude"},
        tokens=FAKE_TOKENS,
    )
    open_ai_tab(page)

    page.fill(CLAUDE_MAX_TOKENS, "12345")
    page.wait_for_timeout(200)

    page.select_option("#LLM_PROVIDER", "openai")
    page.wait_for_timeout(300)
    assert modal_visible(page), "switching away from unsaved edits must ask first"

    page.click("#modal-cancel")
    page.wait_for_timeout(300)
    state = page.evaluate(
        f"""() => ({{
            provider: document.getElementById('LLM_PROVIDER').value,
            value: document.querySelector('{CLAUDE_MAX_TOKENS}').value,
            frame_visible: !document.getElementById('provider-claude').classList.contains('hidden-frame'),
        }})"""
    )
    assert state["provider"] == "claude", "Cancel must stay on the edited provider"
    assert state["value"] == "12345", "Cancel must keep the user's edit"
    assert state["frame_visible"]


def test_switch_away_with_unsaved_edits_ok_reverts_and_switches(open_page):
    page = open_page(
        {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "claude"},
        tokens=FAKE_TOKENS,
    )
    open_ai_tab(page)

    original = page.evaluate(f"document.querySelector('{CLAUDE_MAX_TOKENS}').value")
    page.fill(CLAUDE_MAX_TOKENS, "abc")
    page.wait_for_timeout(200)

    page.select_option("#LLM_PROVIDER", "openai")
    page.wait_for_timeout(300)
    page.click("#modal-ok")
    page.wait_for_timeout(400)

    state = page.evaluate(
        f"""() => ({{
            provider: document.getElementById('LLM_PROVIDER').value,
            value: document.querySelector('{CLAUDE_MAX_TOKENS}').value,
            claude_hidden: document.getElementById('provider-claude').classList.contains('hidden-frame'),
            openai_visible: !document.getElementById('provider-openai').classList.contains('hidden-frame'),
            dirty_left_behind: !!document.getElementById('provider-claude').querySelector('.dirty-field'),
            apply_disabled: document.getElementById('btn-apply').disabled,
        }})"""
    )
    assert state["provider"] == "openai", "OK must complete the switch"
    assert state["value"] == original, "OK must revert the edit to the saved value"
    assert state["claude_hidden"] and state["openai_visible"]
    assert not state["dirty_left_behind"], "no invisible dirty state may remain"
    assert not state["apply_disabled"], "the provider change itself remains applyable"


def test_full_roundtrip_leaves_no_trace(open_page):
    page = open_page(
        {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai"},
        tokens=FAKE_TOKENS,
    )
    open_ai_tab(page)

    page.select_option("#LLM_PROVIDER", "claude")
    page.wait_for_timeout(300)
    original = page.evaluate(f"document.querySelector('{CLAUDE_MAX_TOKENS}').value")
    page.fill(CLAUDE_MAX_TOKENS, "abc")
    page.wait_for_timeout(200)

    page.select_option("#LLM_PROVIDER", "openai")
    page.wait_for_timeout(300)
    assert modal_visible(page)
    page.click("#modal-ok")
    page.wait_for_timeout(400)

    state = page.evaluate(
        f"""() => ({{
            provider: document.getElementById('LLM_PROVIDER').value,
            value: document.querySelector('{CLAUDE_MAX_TOKENS}').value,
            any_dirty: !!document.querySelector('.dirty-field'),
            apply_disabled: document.getElementById('btn-apply').disabled,
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
        }})"""
    )
    assert state["provider"] == "openai"
    assert state["value"] == original, "the typo must be gone"
    assert not state["any_dirty"], "the round trip must leave zero dirty fields"
    assert state["apply_disabled"], "nothing to apply — state matches disk again"
    assert not state["banner_visible"]


def test_scenario_fix_only_active_provider_then_apply(open_page, tmp_path):
    page = open_page(
        {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "claude",
         "LLM_PROVIDERS": dict(BROKEN_THREE)},
        tokens=FAKE_TOKENS,
    )
    state = banner_and_errors(page)
    assert state["banner_visible"], "the ACTIVE provider's breakage must be shown"
    assert state["errors"] == ["LLM_PROVIDERS.claude.max_tokens"], \
        "errors must concern the active provider ONLY — gemini/mistral stay silent"

    open_ai_tab(page)
    page.fill(CLAUDE_MAX_TOKENS, "4096")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function("document.getElementById('btn-apply').disabled")

    state = banner_and_errors(page)
    assert not state["banner_visible"], "fixing the active provider is enough"
    assert state["errors"] == []
    assert state["apply_disabled"], "clean saved state after Apply"

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["LLM_PROVIDERS"]["claude"]["max_tokens"] == 4096
    assert saved["LLM_PROVIDERS"]["gemini"]["max_tokens"] == "abc", \
        "non-selected garbage is stored verbatim, not repaired"
    assert saved["LLM_PROVIDERS"]["mistral"]["max_tokens"] == "abc"


def test_scenario_switch_to_broken_provider_then_apply(open_page):
    page = open_page(
        {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai",
         "LLM_PROVIDERS": dict(BROKEN_THREE)},
        tokens=FAKE_TOKENS,
    )
    state = banner_and_errors(page)
    assert not state["banner_visible"], "broken NON-selected providers = no banner"
    assert state["errors"] == []

    open_ai_tab(page)
    page.select_option("#LLM_PROVIDER", "claude")
    page.wait_for_timeout(300)
    assert not modal_visible(page), "disk values are not unsaved edits — no modal"

    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    )
    state = banner_and_errors(page)
    assert state["banner_visible"], "now claude IS selected, so its error blocks Apply"
    assert "LLM_PROVIDERS.claude.max_tokens" in state["errors"]
    field = page.evaluate(
        f"""() => {{
        const el = document.querySelector('{CLAUDE_MAX_TOKENS}');
        return {{ visible: el.offsetParent !== null,
                  highlighted: el.classList.contains('error-field') }};
    }}"""
    )
    assert field["visible"] and field["highlighted"], \
        "the blocking field is on screen and highlighted — never hidden"


def test_scenario_escape_broken_active_by_choosing_healthy_provider(open_page, tmp_path):
    page = open_page(
        {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "claude",
         "LLM_PROVIDERS": dict(BROKEN_THREE)},
        tokens=FAKE_TOKENS,
    )
    assert banner_and_errors(page)["banner_visible"]

    open_ai_tab(page)
    page.select_option("#LLM_PROVIDER", "openai")
    page.wait_for_timeout(300)
    assert not modal_visible(page)

    page.click("#btn-apply")
    page.wait_for_function("document.getElementById('btn-apply').disabled")
    state = banner_and_errors(page)
    assert not state["banner_visible"], "a healthy selected provider = clean Apply"
    assert state["errors"] == []
    assert state["apply_disabled"]

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["LLM_PROVIDER"] == "openai"
    assert saved["LLM_PROVIDERS"]["claude"]["max_tokens"] == "abc", \
        "the broken ex-active provider is left alone for later"


def test_switch_without_edits_needs_no_modal(open_page):
    page = open_page(
        {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "claude"},
        tokens=FAKE_TOKENS,
    )
    open_ai_tab(page)
    page.select_option("#LLM_PROVIDER", "openai")
    page.wait_for_timeout(300)
    assert not modal_visible(page), "clean frames switch without questions"
    assert page.evaluate(
        "!document.getElementById('provider-openai').classList.contains('hidden-frame')"
    )
