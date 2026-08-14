# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


SENTINEL = "draft-sentinel-must-not-persist"

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
    page = open_page({})
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
    assert state["prompt"] == \
        "Please accurately extract and transcribe all text from these images.", \
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
