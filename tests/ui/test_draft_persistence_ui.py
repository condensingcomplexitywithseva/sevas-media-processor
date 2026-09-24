# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import json
import pytest

SENTINEL = "draft-sentinel-must-not-persist"
DISK_PROMPT = "the prompt that is on disk"

ALLOWED_KEY = "app_lang"
ALLOWED_PREFIX = "translations_"


def make_a_draft(page):
    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)
    page.fill('input[name="JPEG_QUALITY"]', "77")

    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    page.fill("#user_text_input", SENTINEL)
    page.wait_for_timeout(200)
    assert page.evaluate("window.hasUnsavedEdits()"), "the setup did not go dirty"


def test_reload_discards_unsaved_edits(open_page):
    page = open_page({"LLM_USER_PROMPT": DISK_PROMPT})
    make_a_draft(page)

    page.reload()
    page.wait_for_selector("body.lang-loaded", timeout=15000)
    page.wait_for_timeout(400)

    state = page.evaluate(
        """() => ({
            quality: document.querySelector('input[name="JPEG_QUALITY"]').value,
            prompt: document.getElementById('user_text_input').value,
            unsaved: window.hasUnsavedEdits(),
            apply_disabled: document.getElementById('btn-apply').disabled,
            dirty_count: document.querySelectorAll('.dirty-field').length,
        })"""
    )
    assert state["quality"] == "90", "the number edit survived the reload"
    assert SENTINEL not in state["prompt"], "the prompt edit survived the reload"
    assert state["prompt"] == DISK_PROMPT, \
        "the prompt must come back as the disk value"
    assert not state["unsaved"], "a reloaded window must start clean"
    assert state["apply_disabled"]
    assert state["dirty_count"] == 0


def test_no_draft_ever_reaches_localstorage(open_page):
    page = open_page({})

    def stored():
        return page.evaluate(
            """() => Object.fromEntries(
                   Object.keys(localStorage).map(k => [k, localStorage.getItem(k)]))"""
        )

    make_a_draft(page)
    while_dirty = stored()

    page.reload()
    page.wait_for_selector("body.lang-loaded", timeout=15000)
    page.wait_for_timeout(400)
    after_reload = stored()

    for when, entries in (("while dirty", while_dirty), ("after reload", after_reload)):
        unexpected = sorted(
            k for k in entries
            if k != ALLOWED_KEY and not k.startswith(ALLOWED_PREFIX)
        )
        assert not unexpected, f"unexpected localStorage keys {when}: {unexpected}"
        leaked = sorted(k for k, v in entries.items() if SENTINEL in (v or ""))
        assert not leaked, f"the draft text landed in localStorage {when}: {leaked}"



@pytest.mark.parametrize("quality_draft", ["77", "abc"], ids=["valid", "invalid-then-repaired"])
def test_multi_tab_drafts_survive_navigation_until_global_apply(open_page, tmp_path, quality_draft):
    page = open_page({})
    before = (tmp_path / "settings.json").read_bytes()
    edits = [
        ("general", "LOGGING_LEVEL", "WARNING", True),
        ("output", "JPEG_QUALITY", quality_draft, False),
        ("images", "IMAGE_RANGE", "1-3", False),
        ("docs", "PDF_SCALE", "3", False),
    ]
    for tab, field, value, select in edits:
        page.click(f'.sidebar-tab[data-tab="{tab}"]')
        control = page.locator(f'[name="{field}"]')
        if select:
            control.select_option(value)
        else:
            control.fill(value)
    for tab, field, value, _ in reversed(edits):
        page.click(f'.sidebar-tab[data-tab="{tab}"]')
        control = page.locator(f'[name="{field}"]')
        assert control.input_value() == value
        assert "dirty-field" in (control.get_attribute("class") or "")
        assert page.evaluate("name => window.draftState[name]", field) == value
    assert (tmp_path / "settings.json").read_bytes() == before
    assert page.locator("#btn-apply").is_enabled()
    assert page.locator("#btn-start").is_disabled()
    if quality_draft == "abc":
        with page.expect_response("**/api/settings/commit") as reply:
            page.click("#btn-apply")
        assert reply.value.status == 400
        page.wait_for_function("!window.settingsSavePending")
        assert (tmp_path / "settings.json").read_bytes() == before
        for _, field, value, _ in edits:
            assert page.evaluate("name => window.draftState[name]", field) == value
        page.click('.sidebar-tab[data-tab="output"]')
        page.fill('[name="JPEG_QUALITY"]', "77")
    with page.expect_response("**/api/settings/commit") as reply:
        page.click("#btn-apply")
    assert reply.value.status == 200
    page.wait_for_function("!window.settingsSavePending && !window.hasUnsavedEdits()")
    stored = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert {field: stored[field] for _, field, _, _ in edits} == {
        "LOGGING_LEVEL": "WARNING", "JPEG_QUALITY": 77, "IMAGE_RANGE": "1-3", "PDF_SCALE": 3,
    }
    assert page.locator("#btn-start").is_enabled()
