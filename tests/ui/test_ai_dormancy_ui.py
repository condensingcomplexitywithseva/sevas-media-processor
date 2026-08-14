# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


RETRIES_FIELD = 'input[name="LLM_MAX_RETRIES"]'

GARBAGE_RETRIES = {"LLM_MAX_RETRIES": "abc"}


def test_ai_off_scalar_garbage_shows_nothing(open_page):
    page = open_page({"ENABLE_LLM_INFERENCE": False, **GARBAGE_RETRIES})
    state = page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            disk_errors: Object.keys(window.diskErrors || {}),
            start_disabled: document.getElementById('btn-start').disabled,
        })"""
    )
    assert not state["banner_visible"], "AI is off — a broken AI scalar must not banner"
    assert state["disk_errors"] == []
    assert not state["start_disabled"], "Start must not be blocked"


def test_toggling_ai_on_surfaces_scalar_garbage(open_page):
    page = open_page(
        {"ENABLE_LLM_INFERENCE": False, **GARBAGE_RETRIES},
        tokens={"openai": "sk-fake0123456789abcdef0123456789abcdef"},
    )
    assert not page.evaluate(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    ), "sanity: clean start while AI is off"

    page.select_option("#ENABLE_LLM_INFERENCE", "true")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    )

    state = page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            errors: Object.keys(window.draftErrors || {}),
        })"""
    )
    assert state["banner_visible"], "AI on: the dormant garbage must now block Apply"
    assert state["errors"] == ["LLM_MAX_RETRIES"], \
        "with a token present the ONLY error is the scalar under test"

    page.click('[data-tab="ai"]')
    page.wait_for_timeout(400)
    field = page.evaluate(
        f"""() => {{
        const el = document.querySelector('{RETRIES_FIELD}');
        return {{ visible: el.offsetParent !== null,
                  highlighted: el.classList.contains('error-field') }};
    }}"""
    )
    assert field["visible"] and field["highlighted"], \
        "the blocking field is on screen and highlighted — never hidden"
