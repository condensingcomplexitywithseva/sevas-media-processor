# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import pytest

DISABLEABLE = {"btn-start", "btn-stop", "btn-apply", "btn-discard"}


def snapshot_buttons(page):
    return page.evaluate(
        """() => {
            const out = {};
            document.querySelectorAll('button[id]').forEach(b => {
                const s = getComputedStyle(b);
                if (s.display === 'none' || s.visibility === 'hidden') return;
                out[b.id] = {
                    disabled: b.disabled,
                    opacity: parseFloat(s.opacity),
                    background: s.backgroundColor,
                    color: s.color,
                    cursor: s.cursor,
                    title: b.title || '',
                };
            });
            return out;
        }"""
    )


def appearance(look):
    return (look["opacity"], look["background"], look["color"])


def assert_affordance(state_name, buttons):
    for button_id, look in buttons.items():
        if look["disabled"]:
            assert look["cursor"] == "not-allowed", (
                f"[{state_name}] #{button_id} is disabled but its cursor is "
                f"{look['cursor']!r}, so hovering does not say so either"
            )


def dirty_the_form(page):
    page.evaluate(
        """() => {
            const el = document.getElementById('JPEG_QUALITY');
            el.value = String((parseInt(el.value, 10) || 50) + 1);
            el.dispatchEvent(new Event('input', {bubbles: true}));
        }"""
    )
    page.wait_for_timeout(150)


def test_no_disabled_button_ever_looks_clickable(open_page, tmp_path):
    page = open_page({})
    seen_disabled = set()
    looks = {}

    def check(state_name):
        buttons = snapshot_buttons(page)
        assert_affordance(state_name, buttons)
        for button_id, look in buttons.items():
            looks.setdefault(button_id, {})[look["disabled"]] = appearance(look)
        seen_disabled.update(bid for bid, look in buttons.items() if look["disabled"])
        return buttons

    clean = check("clean")
    assert clean["btn-start"]["disabled"] is False
    assert clean["btn-apply"]["disabled"] is True
    assert clean["btn-discard"]["disabled"] is True
    assert clean["btn-stop"]["disabled"] is True

    dirty_the_form(page)
    dirty = check("dirty")
    assert dirty["btn-apply"]["disabled"] is False
    assert dirty["btn-discard"]["disabled"] is False
    assert dirty["btn-start"]["disabled"] is True, \
        "Start must not run against an unapplied draft"
    assert dirty["btn-start"]["title"], "a blocked Start should say why on hover"

    page.click("#btn-discard")
    page.wait_for_timeout(300)
    check("after discard")

    page.evaluate(
        """() => {
            window.runActive = true;
            window.updateGlobalControls();
            window.setButtonState(document.getElementById('btn-stop'), true);
        }"""
    )
    running = check("running")
    assert running["btn-start"]["disabled"] is True
    assert running["btn-stop"]["disabled"] is False

    page.evaluate(
        """() => window.setButtonState(document.getElementById('btn-stop'), false,
                                       'Stopping - waiting for the current file to finish.')"""
    )
    stopping = check("stopping")
    assert stopping["btn-start"]["disabled"] is True
    assert stopping["btn-stop"]["disabled"] is True

    page.evaluate("() => { window.runActive = false; window.updateGlobalControls(); }")
    finished = check("finished")
    assert finished["btn-start"]["disabled"] is False

    missed = DISABLEABLE - seen_disabled
    assert not missed, (
        "these buttons are disableable but no scenario above ever saw them "
        f"disabled, so the sweep did not test them: {sorted(missed)}"
    )

    compared = 0
    for button_id, states in looks.items():
        if True in states and False in states:
            compared += 1
            assert states[True] != states[False], (
                f"#{button_id} looks IDENTICAL enabled and disabled "
                f"{states[True]} - nothing on screen tells the user it is dead"
            )
    assert compared >= len(DISABLEABLE), (
        f"only {compared} buttons were seen in both states; the comparison "
        "above cannot catch anything for the rest"
    )


def test_a_blocked_start_explains_itself_in_every_state(open_page):
    page = open_page({})

    dirty_the_form(page)
    pending = page.evaluate("() => document.getElementById('btn-start').title")

    page.click("#btn-discard")
    page.wait_for_timeout(300)
    page.evaluate("() => { window.runActive = true; window.updateGlobalControls(); }")
    running = page.evaluate("() => document.getElementById('btn-start').title")

    assert pending and running, "a disabled Start must always carry a reason"
    assert pending != running, (
        "'unapplied changes' and 'already running' are different problems "
        f"and must not share one message (both said {pending!r})"
    )


@pytest.mark.parametrize("language", ["en", "ru"])
def test_the_disabled_reasons_are_translated(open_page, language):
    page = open_page({})
    page.evaluate(f"changeLanguage('{language}')")
    page.wait_for_timeout(200)

    reasons = page.evaluate(
        """() => ['warn_run_in_progress', 'warn_already_stopping',
                  'warn_no_run_to_stop', 'warn_nothing_to_apply']
                 .map(k => window.getT(k, '__MISSING__'))"""
    )
    assert "__MISSING__" not in reasons, f"untranslated key in {language}: {reasons}"
    assert all(text.strip() for text in reasons), reasons
