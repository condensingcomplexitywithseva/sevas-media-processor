# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import pytest


def test_notice_provider_link_reaches_the_named_control_after_provider_changes(open_page, tmp_path):
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'openai'},
                     tokens={'openai': 'sk-fake0123456789abcdef0123456789abcdef'})
    page.evaluate("switchTab('ai')")
    page.evaluate("""() => {
        notice({id: 'start', key: 'err_settings_invalid',
            field: 'LLM_PROVIDERS.openai.max_tokens', summaryKey: 'notice_start_setting'});
    }""")
    page.locator('[name="LLM_PROVIDER"]').select_option('ollama')
    before = page.evaluate('JSON.stringify(window.draftState)')
    disk = (tmp_path / 'settings.json').read_bytes()
    page.locator('[data-notice-id="start"] a').click()
    page.wait_for_timeout(400)
    target = page.locator('[name="LLM_PROVIDERS.openai.max_tokens"]')
    assert target.is_visible(), 'A live setting link must not silently target a hidden provider frame'
    assert target.evaluate('(el) => document.activeElement === el')
    assert page.evaluate('JSON.stringify(window.draftState)') == before
    assert (tmp_path / 'settings.json').read_bytes() == disk


@pytest.mark.parametrize('ai_enabled', [True, False])
def test_provider_inspection_preserves_drafts_and_blocks_edits(open_page, tmp_path, ai_enabled):
    from pathlib import Path

    page = open_page({'ENABLE_LLM_INFERENCE': ai_enabled, 'LLM_PROVIDER': 'ollama'})
    page.set_viewport_size({'width': 1280, 'height': 800})
    page.evaluate("switchTab('ai')")
    if ai_enabled:
        page.locator('[name="LLM_PROVIDERS.ollama.model"]').fill('unsaved-model')
    disk = (tmp_path / 'settings.json').read_bytes()
    draft = page.evaluate('JSON.stringify(draftState)')
    dirty = page.evaluate('hasUnsavedEdits()')
    for locale in sorted((Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json')):
        page.evaluate('changeLanguage', locale.stem)
        page.evaluate("goToField('LLM_PROVIDERS.openai.max_tokens')")
        target = page.locator('[name="LLM_PROVIDERS.openai.max_tokens"]')
        page.wait_for_function("document.activeElement.name === 'LLM_PROVIDERS.openai.max_tokens'")
        assert target.is_visible()
        assert target.evaluate('el => el.readOnly')
        assert page.locator('#provider-ollama').is_visible()
        assert page.locator('#LLM_PROVIDER').input_value() == 'ollama'
        before = target.input_value()
        page.keyboard.type('123456')
        assert target.input_value() == before
        toggle = page.locator('#req_max_openai')
        checked = toggle.is_checked()
        toggle.focus()
        page.keyboard.press('Space')
        assert toggle.is_checked() == checked
        assert page.locator('#provider-openai .btn-default').first.is_disabled()
        assert page.evaluate('JSON.stringify(draftState)') == draft
        assert page.evaluate('hasUnsavedEdits()') == dirty
        note = page.locator('[data-notice-id="provider-inspection"]')
        assert note.is_visible()
        assert note.evaluate('(el) => el.scrollWidth <= el.clientWidth')
        page.screenshot(path=str(tmp_path / f'provider-inspection-{locale.stem}-{ai_enabled}.png'))
        page.locator('[data-notice-id="provider-inspection"] button').click()
        assert not target.is_visible()
    assert (tmp_path / 'settings.json').read_bytes() == disk
