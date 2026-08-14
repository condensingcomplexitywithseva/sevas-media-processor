# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json


def test_retyping_the_same_value_is_not_dirty(open_page):
    page = open_page({})
    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)

    page.fill('input[name="JPEG_QUALITY"]', "77")
    page.wait_for_timeout(200)
    assert page.evaluate("!document.getElementById('btn-apply').disabled")

    page.fill('input[name="JPEG_QUALITY"]', "090")
    page.wait_for_timeout(200)
    state = page.evaluate(
        """() => ({
            apply_disabled: document.getElementById('btn-apply').disabled,
            field_dirty: document.querySelector('input[name="JPEG_QUALITY"]').classList.contains('dirty-field'),
            unsaved: window.hasUnsavedEdits(),
        })"""
    )
    assert state["apply_disabled"], "Apply must disable when the draft equals disk again"
    assert not state["field_dirty"], "canonically-equal value must clear the dirty marker"
    assert not state["unsaved"]


def test_unparseable_number_survives_apply_and_shows_error(open_page):
    page = open_page({})
    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)

    page.fill('input[name="MAX_DIMENSION"]', "abc")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    )

    state = page.evaluate(
        """() => {
            const el = document.querySelector('input[name="MAX_DIMENSION"]');
            return {
                value: el.value,
                highlighted: el.classList.contains('error-field'),
                message: document.getElementById('err-MAX_DIMENSION').textContent,
                banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            };
        }"""
    )
    assert state["value"] == "abc", "the raw text must survive the failed Apply"
    assert state["highlighted"], "the field must be highlighted"
    assert "integer" in state["message"].lower(), f"expected the integer message, got {state['message']!r}"
    assert state["banner_visible"], "the global banner must show for a failed Apply"


def test_checkbox_group_and_dual_prompt_discard_roundtrip(open_page):
    page = open_page({"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "ollama"})

    page.evaluate("window.switchTab('general')")
    page.wait_for_timeout(400)
    page.check('input[name="NO_RETRY_STATUSES"][value="failure"]')
    page.wait_for_timeout(200)
    assert page.evaluate("window.hasUnsavedEdits()")
    assert page.evaluate(
        """document.querySelector('input[name="NO_RETRY_STATUSES"][value="failure"]')
               .classList.contains('dirty-field')"""
    ), "the toggled box must carry the dirty marker"

    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    page.fill("#user_text_input", "custom prompt text")
    page.wait_for_timeout(200)

    page.click("#btn-discard")
    page.wait_for_timeout(400)

    state = page.evaluate(
        """() => ({
            unsaved: window.hasUnsavedEdits(),
            apply_disabled: document.getElementById('btn-apply').disabled,
            prompt: document.getElementById('user_text_input').value,
            failure_checked: document.querySelector('input[name="NO_RETRY_STATUSES"][value="failure"]').checked,
            ok_checked: document.querySelector('input[name="NO_RETRY_STATUSES"][value="ok"]').checked,
            dirty_count: document.querySelectorAll('.dirty-field').length,
        })"""
    )
    assert not state["unsaved"], "Discard must return to the clean state"
    assert state["apply_disabled"]
    assert state["prompt"] == "Please accurately extract and transcribe all text from these images.", \
        "the prompt textarea must revert to the disk value"
    assert not state["failure_checked"], "the toggled box must revert"
    assert state["ok_checked"], "the originally-checked box must stay"
    assert state["dirty_count"] == 0, "no dirty markers may survive a Discard"


def test_manual_revert_after_failed_apply_restores_clean_banner(open_page):
    page = open_page({})
    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)

    page.fill('input[name="JPEG_QUALITY"]', "500")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    )

    failed = page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            message: document.getElementById('err-JPEG_QUALITY').textContent,
        })"""
    )
    assert failed["banner_visible"], "failed Apply must show the banner"
    assert failed["message"].strip(), "failed Apply must show the field message"

    page.fill('input[name="JPEG_QUALITY"]', "90")
    page.wait_for_timeout(200)

    reverted = page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            message: document.getElementById('err-JPEG_QUALITY').textContent.trim(),
            highlighted: document.querySelector('input[name="JPEG_QUALITY"]').classList.contains('error-field'),
            start_enabled: !document.getElementById('btn-start').disabled,
        })"""
    )
    assert not reverted["banner_visible"], "banner must clear when the draft equals disk again"
    assert reverted["message"] == "", "the field message must clear"
    assert not reverted["highlighted"], "the field highlight must clear"
    assert reverted["start_enabled"], "Start must re-enable on a clean, valid state"


def test_assigning_draft_errors_fails_loudly(open_page):
    page = open_page({})
    threw = page.evaluate(
        """() => {
            try { window.draftErrors = {}; return false; }
            catch (e) { return String(e).includes('derived'); }
        }"""
    )
    assert threw, "assigning window.draftErrors must throw"


def test_apply_normalizes_spelling_on_disk(open_page, tmp_path):
    page = open_page({})
    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)

    page.fill('input[name="JPEG_QUALITY"]', "077")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function("document.getElementById('btn-apply').disabled")

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["JPEG_QUALITY"] == 77, "the backend must store the normalized integer"
    state = page.evaluate(
        """() => ({
            apply_disabled: document.getElementById('btn-apply').disabled,
            dirty_count: document.querySelectorAll('.dirty-field').length,
        })"""
    )
    assert state["apply_disabled"], "UI must be clean after the normalizing Apply"
    assert state["dirty_count"] == 0
