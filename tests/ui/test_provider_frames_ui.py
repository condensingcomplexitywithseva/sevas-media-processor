# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

from fake_llm.harness import READY_ENTRY, ready_ai

CUSTOM_MAX_TOKENS = 'input[name="LLM_PROVIDERS.custom.max_tokens"]'
LEFT_OVER = "left-over-provider"
PRESERVATION_DATA = {"empty": {}, "literal.key": "007", "large": 9007199254740993,
                     "array": [None, False, {}, {"a.b": [9007199254740993]}]}
LOCALES = sorted(path.stem for path in (Path(__file__).resolve().parents[2] / 'src' / 'locales').glob('*.json'))


def frame_ids(page):
    return set(page.eval_on_selector_all('.provider-frame', 'frames => frames.map(f => f.id)'))


def open_ai_tab(page):
    page.click('[data-tab="ai"]')
    page.wait_for_timeout(400)


def page_state(page):
    return page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            disk_errors: Object.keys(window.diskErrors || {}),
            errors: Object.keys(window.draftErrors || {}),
            start_disabled: document.getElementById('btn-start').disabled,
        })"""
    )


def apply_quality(page, tmp_path, quality=81):
    page.evaluate("window.switchTab('output')")
    page.fill('input[name="JPEG_QUALITY"]', str(quality))
    with page.expect_response("**/api/settings/commit") as response:
        page.click("#btn-apply")
    assert response.value.status == 200, response.value.json()
    page.wait_for_function("!window.settingsSavePending && document.getElementById('btn-apply').disabled")
    return json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))


def test_disk_garbage_in_non_selected_provider_shows_no_error(open_page):
    settings = ready_ai()
    settings["LLM_PROVIDERS"][LEFT_OVER] = {"max_tokens": "abc"}
    state = page_state(open_page(settings))
    assert not state["banner_visible"], "no banner for a non-selected provider's value"
    assert state["disk_errors"] == []
    assert not state["start_disabled"], "Start must not be blocked"


def test_ai_off_means_no_ai_errors_even_for_selected_provider(open_page):
    settings = ready_ai(max_tokens="abc")
    settings["ENABLE_LLM_INFERENCE"] = False
    state = page_state(open_page(settings))
    assert not state["banner_visible"], "AI is off — nothing AI-related may block"
    assert state["disk_errors"] == []
    assert not state["start_disabled"]


def test_garbage_in_selected_provider_still_shows_visible_error(open_page):
    page = open_page(ready_ai(max_tokens="abc"))
    state = page_state(page)
    assert state["banner_visible"], "selected provider's broken value must show the banner"
    assert state["errors"] == ["LLM_PROVIDERS.custom.max_tokens"]
    page.click("#global-error-text .banner-jump-btn")
    page.wait_for_timeout(600)
    state = page.evaluate(
        f"""() => {{
        const el = document.querySelector('{CUSTOM_MAX_TOKENS}');
        return {{
            visible: el.offsetParent !== null,
            // The pulse lands on the form-group wrapper (test_error_navigation_ui.py).
            flashed: el.closest('.form-group').classList.contains('error-flash'),
        }};
    }}"""
    )
    assert state["visible"] and state["flashed"], "jump must land on the visible broken field"


@pytest.mark.parametrize("providers", [None, [], "dormant", {LEFT_OVER: None},
                                      {"newprovider": {"url": "http://localhost", "model": "future"}}])
def test_dormant_provider_shapes_render_and_survive_apply(open_page, tmp_path, providers):
    page = open_page({"ENABLE_LLM_INFERENCE": False, "LLM_PROVIDERS": providers})
    frames = frame_ids(page)
    assert "provider-custom" in frames
    assert not {"provider-newprovider", f"provider-{LEFT_OVER}"} & frames, \
        "an entry the edition does not ship never becomes a frame"
    saved = apply_quality(page, tmp_path)
    assert saved["LLM_PROVIDERS"] == providers


def test_missing_ai_fields_stay_missing_after_unrelated_apply(open_page, tmp_path):
    page = open_page({"ENABLE_LLM_INFERENCE": False})
    saved = apply_quality(page, tmp_path)
    assert "LLM_PROVIDERS" not in saved
    assert "LLM_MAX_RETRIES" not in saved


def test_ai_edits_made_before_disabling_are_saved(open_page, tmp_path):
    page = open_page(ready_ai(future=PRESERVATION_DATA))
    open_ai_tab(page)
    page.fill('input[name="LLM_PROVIDERS.custom.model"]', "edited-model")
    page.evaluate("window.switchTab('general')")
    page.select_option('[name="ENABLE_LLM_INFERENCE"]', 'false')
    saved = apply_quality(page, tmp_path)
    assert saved["ENABLE_LLM_INFERENCE"] is False
    assert saved["LLM_PROVIDERS"] == {
        "custom": {**READY_ENTRY, "model": "edited-model", "future": PRESERVATION_DATA}}


def test_large_known_integer_can_be_edited_without_rounding(open_page, tmp_path):
    page = open_page({**ready_ai(), "LLM_MAX_RETRIES": 9007199254740993})
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


@pytest.mark.parametrize('locale', LOCALES)
def test_unknown_provider_is_visible_and_can_be_repaired(open_page, tmp_path, locale):
    page = open_page({"ENABLE_LLM_INFERENCE": False, "LLM_PROVIDER": "future-provider",
                      "LLM_PROVIDERS": {"future-provider": PRESERVATION_DATA, "custom": READY_ENTRY}})
    print(f"Provider visual evidence: {tmp_path}")
    page.set_viewport_size({'width': 1280, 'height': 800})
    page.evaluate('(locale) => changeLanguage(locale)', locale)
    open_ai_tab(page)
    assert page.locator('#LLM_PROVIDER').input_value() == 'future-provider'
    assert "provider-future-provider" not in frame_ids(page)
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
    assert page.locator('#err-LLM_PROVIDER').inner_text() == '⚠️ ' + expected_error
    page.locator('#LLM_PROVIDER').scroll_into_view_if_needed()
    page.screenshot(path=str(tmp_path / f'provider-error-{locale}.png'))
    page.select_option('#LLM_PROVIDER', 'custom')
    saved = apply_quality(page, tmp_path)
    assert saved['LLM_PROVIDER'] == 'custom'
    assert saved['LLM_PROVIDERS'] == {'future-provider': PRESERVATION_DATA, 'custom': READY_ENTRY}
