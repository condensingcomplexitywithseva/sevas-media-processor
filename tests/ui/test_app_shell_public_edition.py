# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

from test_app_shell import assert_role_reference


def test_inspection_gallery_keeps_the_approved_roles(open_page, tmp_path):
    page = open_page({"LLM_PROVIDER": "gemini"})
    page.set_viewport_size({'width': 1280, 'height': 800})
    locales = sorted(p.stem for p in (Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json'))
    for language in locales:
        page.evaluate('changeLanguage', language)
        page.evaluate("goToField('LLM_PROVIDERS.openai.max_tokens')")
        page.wait_for_function("document.activeElement.name === 'LLM_PROVIDERS.openai.max_tokens'")
        assert page.locator('[data-notice-id="provider-inspection"]').is_visible()
        assert page.locator('#max_tok_openai').evaluate('el => el.readOnly')
        assert_role_reference(page)
        page.screenshot(path=str(tmp_path / ('inspection-gallery-' + language + '.png')))
        page.locator('[data-notice-id="provider-inspection"] button').click()
