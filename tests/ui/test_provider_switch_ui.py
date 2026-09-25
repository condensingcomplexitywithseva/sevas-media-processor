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


SUPPORTED_FRAMES = {f"provider-{name}" for name in (
    "openai", "claude", "gemini", "deepseek", "mistral", "zai", "ollama", "lm-studio", "custom")}


def frame_ids(page):
    return set(page.eval_on_selector_all('.provider-frame', 'frames => frames.map(f => f.id)'))


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
    page.wait_for_function("!window.settingsSavePending && document.getElementById('btn-apply').disabled")

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
    page.wait_for_function("!window.settingsSavePending && document.getElementById('btn-apply').disabled")
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


import pytest

PRESERVATION_DATA = {"empty": {}, "literal.key": "007", "large": 9007199254740993,
                     "array": [None, False, {}, {"a.b": [9007199254740993]}]}


def apply_quality(page, tmp_path, quality=81):
    page.evaluate("window.switchTab('output')")
    page.fill('input[name="JPEG_QUALITY"]', str(quality))
    with page.expect_response("**/api/settings/commit") as response:
        page.click("#btn-apply")
    assert response.value.status == 200, response.value.json()
    page.wait_for_function("!window.settingsSavePending && document.getElementById('btn-apply').disabled")
    return json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("enabled,selected", [(True, "ollama"), (False, "ollama"), (False, "claude")])
def test_unrelated_apply_preserves_complete_inactive_data(open_page, tmp_path, enabled, selected):
    entry = {"max_tokens": "123", "require_max_tokens": "false", "future": PRESERVATION_DATA}
    original = {"ENABLE_LLM_INFERENCE": enabled, "LLM_PROVIDER": selected,
                "LLM_PROVIDERS": {"claude": entry}, "FUTURE_TOP": PRESERVATION_DATA}
    page = open_page(original)
    for quality in (81, 82):
        saved = apply_quality(page, tmp_path, quality)
        assert saved["JPEG_QUALITY"] == quality
        assert "ENV_TOKENS" not in saved
        assert json.dumps(saved["LLM_PROVIDERS"], sort_keys=True) == json.dumps({"claude": entry}, sort_keys=True)
        assert saved["FUTURE_TOP"] == PRESERVATION_DATA


@pytest.mark.parametrize("providers", [None, [], "dormant", {"claude": None},
                                      {"newprovider": {"url": "http://localhost", "model": "future"}}])
def test_dormant_provider_shapes_render_and_survive_apply(open_page, tmp_path, providers):
    page = open_page({"ENABLE_LLM_INFERENCE": False, "LLM_PROVIDERS": providers})
    assert frame_ids(page) == SUPPORTED_FRAMES
    saved = apply_quality(page, tmp_path)
    assert saved["LLM_PROVIDERS"] == providers


def test_missing_ai_fields_stay_missing_after_unrelated_apply(open_page, tmp_path):
    page = open_page({"ENABLE_LLM_INFERENCE": False})
    saved = apply_quality(page, tmp_path)
    assert "LLM_PROVIDERS" not in saved
    assert "LLM_MAX_RETRIES" not in saved


def test_ai_edits_made_before_disabling_are_saved(open_page, tmp_path):
    page = open_page({"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "ollama",
                      "LLM_PROVIDERS": {"ollama": {"future": PRESERVATION_DATA}}})
    open_ai_tab(page)
    page.fill('input[name="LLM_PROVIDERS.ollama.model"]', "edited-model")
    page.evaluate("window.switchTab('general')")
    page.select_option('[name="ENABLE_LLM_INFERENCE"]', 'false')
    saved = apply_quality(page, tmp_path)
    assert saved["ENABLE_LLM_INFERENCE"] is False
    assert saved["LLM_PROVIDERS"] == {"ollama": {"model": "edited-model", "future": PRESERVATION_DATA}}


def test_provider_revert_and_discard_do_not_materialize_defaults(open_page, tmp_path):
    providers = {"claude": {"max_tokens": "123", "future": PRESERVATION_DATA}}
    page = open_page({"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "ollama", "LLM_PROVIDERS": providers})
    open_ai_tab(page)
    page.fill('input[name="LLM_PROVIDERS.ollama.model"]', "temporary")
    page.select_option('#LLM_PROVIDER', 'lm-studio')
    page.click('#modal-ok')
    page.evaluate('window.discardGlobalDraft()')
    assert page.locator('#LLM_PROVIDER').input_value() == 'ollama'
    saved = apply_quality(page, tmp_path)
    assert saved['LLM_PROVIDERS'] == providers


@pytest.mark.parametrize("restore_full_draft", [False, True])
def test_preservation_assertion_detects_default_leakage(open_page, tmp_path, restore_full_draft):
    original = {"claude": {"max_tokens": "123", "require_max_tokens": "false"}}
    page = open_page({"ENABLE_LLM_INFERENCE": False, "LLM_PROVIDERS": original})
    if restore_full_draft:
        full_draft = page.evaluate('window.draftState')
        full_draft['JPEG_QUALITY'] = '81'
        page.route('**/api/settings/commit',
                   lambda route: route.continue_(post_data=json.dumps(full_draft)))
    saved = apply_quality(page, tmp_path)

    def assert_preserved():
        assert saved['LLM_PROVIDERS'] == original

    if restore_full_draft:
        with pytest.raises(AssertionError):
            assert_preserved()
    else:
        assert_preserved()


def test_large_known_integer_can_be_edited_without_rounding(open_page, tmp_path):
    page = open_page({"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "ollama",
                      "LLM_MAX_RETRIES": 9007199254740993})
    open_ai_tab(page)
    field = page.locator('[name="LLM_MAX_RETRIES"]')
    assert field.input_value() == '9007199254740993'
    field.fill('9007199254740994')
    assert page.evaluate('window.hasUnsavedEdits()')
    with page.expect_response('**/api/settings/commit') as response:
        page.click('#btn-apply')
    assert response.value.status == 200
    saved = json.loads((tmp_path / 'settings.json').read_text(encoding='utf-8'))
    assert saved['LLM_MAX_RETRIES'] == 9007199254740994


from pathlib import Path
LOCALES = sorted(path.stem for path in (Path(__file__).resolve().parents[2] / 'src' / 'locales').glob('*.json'))


@pytest.mark.parametrize('locale', LOCALES)
def test_unknown_provider_is_visible_and_can_be_repaired(open_page, tmp_path, locale):
    page = open_page({"ENABLE_LLM_INFERENCE": False, "LLM_PROVIDER": "future-provider",
                      "LLM_PROVIDERS": {"future-provider": PRESERVATION_DATA}})
    print(f"Provider visual evidence: {tmp_path}")
    page.set_viewport_size({'width': 1280, 'height': 800})
    page.evaluate('(locale) => changeLanguage(locale)', locale)
    open_ai_tab(page)
    assert page.locator('#LLM_PROVIDER').input_value() == 'future-provider'
    assert frame_ids(page) == SUPPORTED_FRAMES
    assert not page.evaluate('window.hasUnsavedEdits()')
    page.locator('#LLM_PROVIDER').scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / f'provider-dormant-{locale}.png'))
    page.evaluate("window.switchTab('general')")
    page.select_option('#ENABLE_LLM_INFERENCE', 'true')
    with page.expect_response('**/api/settings/commit') as response:
        page.click('#btn-apply')
    assert response.value.status == 400
    assert response.value.json()['errors'] == {
        'LLM_PROVIDER': {'type': 'i18n', 'value': 'err_unknown_provider'}}
    open_ai_tab(page)
    locale_path = Path(__file__).resolve().parents[2] / 'src' / 'locales' / f'{locale}.json'
    expected_error = json.loads(locale_path.read_text(encoding='utf-8'))['err_unknown_provider']
    assert page.locator('#err-LLM_PROVIDER').inner_text() == '\u26a0\ufe0f ' + expected_error
    page.locator('#LLM_PROVIDER').scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / f'provider-error-{locale}.png'))
    page.select_option('#LLM_PROVIDER', 'ollama')
    saved = apply_quality(page, tmp_path)
    assert saved['LLM_PROVIDER'] == 'ollama'
    assert saved['LLM_PROVIDERS'] == {'future-provider': PRESERVATION_DATA}


@pytest.mark.parametrize('providers', [None, {'ollama': None}])
def test_default_button_repairs_malformed_provider_without_saving_other_defaults(open_page, tmp_path, providers):
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'ollama', 'LLM_PROVIDERS': providers})
    open_ai_tab(page)
    url = page.locator('[name="LLM_PROVIDERS.ollama.url"]')
    assert url.input_value() == ''
    page.locator('#provider-ollama .input-row').filter(has=url).locator('button').click()
    saved = apply_quality(page, tmp_path)
    assert set(saved['LLM_PROVIDERS']) == {'ollama'}
    assert saved['LLM_PROVIDERS']['ollama'] == {'url': 'http://localhost:11434/v1/chat/completions'}


def test_boolean_strings_render_without_modifying_inactive_value(open_page, tmp_path):
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'ollama',
                      'LLM_PROVIDERS': {'ollama': {'require_max_tokens': 'false'},
                                        'claude': {'require_max_tokens': 'false'}}})
    open_ai_tab(page)
    assert not page.locator('#req_max_ollama').is_checked()
    assert not page.locator('#req_max_claude').is_checked()
    saved = apply_quality(page, tmp_path)
    assert saved['LLM_PROVIDERS']['claude'] == {'require_max_tokens': 'false'}
