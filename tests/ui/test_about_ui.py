# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from version import APP_LINKS, APP_VERSION


def test_sidebar_brand_shows_name_and_version_on_every_tab(open_page):
    page = open_page({})
    brand = page.locator("#sidebar-brand")
    assert brand.is_visible()
    text = brand.inner_text()
    assert "Seva's Media Processor" in text
    assert f"v{APP_VERSION}" in text
    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    assert page.locator("#sidebar-brand").is_visible()


def test_about_dialog_opens_lists_urls_and_closes(open_page):
    page = open_page({})
    overlay = page.locator("#about-overlay")
    assert not overlay.is_visible()

    page.click("#sidebar-brand")
    page.wait_for_timeout(200)
    assert overlay.is_visible()
    assert f"v{APP_VERSION}" in page.locator("#about-version").inner_text()

    assert page.locator("#about-url-youtube").inner_text() == APP_LINKS["youtube"]
    assert page.locator("#about-url-github").inner_text() == APP_LINKS["github"]

    hrefs = page.eval_on_selector_all(
        "#about-overlay a", "els => els.map(e => e.getAttribute('href'))"
    )
    assert hrefs and all(h == "javascript:void(0)" for h in hrefs)
    onclicks = page.eval_on_selector_all(
        "#about-overlay a", "els => els.map(e => e.getAttribute('onclick'))"
    )
    assert all("openExternalLink" in oc for oc in onclicks)

    page.keyboard.press("Escape")
    page.wait_for_timeout(200)
    assert not overlay.is_visible()
    page.click("#sidebar-brand")
    page.wait_for_timeout(200)
    assert overlay.is_visible()
    page.click("#about-ok")
    page.wait_for_timeout(200)
    assert not overlay.is_visible()


def test_about_copy_buttons_put_the_exact_url_on_the_clipboard(open_page):
    page = open_page({})
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.click("#sidebar-brand")
    page.wait_for_timeout(200)
    for key in ("youtube", "github"):
        page.click(f"#about-copy-{key}")
        page.wait_for_timeout(300)
        clip = page.evaluate("navigator.clipboard.readText()")
        assert clip == APP_LINKS[key], f"clipboard does not carry the {key} URL"


def test_about_strings_translate(open_page):
    page = open_page({})
    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(400)
    page.click("#sidebar-brand")
    page.wait_for_timeout(200)
    text = page.locator("#about-dialog").inner_text()
    assert "Vsevolod Belonogov" in text
    ru_built_by = page.evaluate("window.currentTranslations['lbl_about_built_by']")
    assert ru_built_by and ru_built_by in text
