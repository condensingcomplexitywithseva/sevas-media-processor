# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import time

from PIL import Image


def test_full_run_reaches_green_done_state(open_page, tmp_path):
    page = open_page({})
    Image.new("RGB", (320, 200), (200, 60, 60)).save(tmp_path / "input" / "probe.png")

    assert page.evaluate("!document.getElementById('btn-start').disabled"), \
        "Start must be enabled for a valid config"
    page.click("#btn-start")

    page.wait_for_function(
        """() => {
            const s = document.getElementById('run-status');
            return getComputedStyle(s).display !== 'none' && s.className.startsWith('status-');
        }""",
        timeout=30000,
    )
    page.wait_for_timeout(500)

    final = page.evaluate(
        """() => ({
            status_cls: document.getElementById('run-status').className,
            bar_cls: document.getElementById('progress-bar').className,
            bar_val: document.getElementById('progress-bar').value,
            start_enabled: !document.getElementById('btn-start').disabled,
            stop_disabled: document.getElementById('btn-stop').disabled,
        })"""
    )
    assert final["status_cls"] == "status-done", f"run must succeed, got {final}"
    assert "progress-done" in final["bar_cls"], "progress bar must turn green"
    assert int(final["bar_val"]) == 100, "progress must reach 100% only at the true end"
    assert final["start_enabled"], "Start must re-enable after the run"
    assert final["stop_disabled"], "Stop must disable after the run"

    jpegs = list((tmp_path / "output").rglob("*.jpg")) + list((tmp_path / "output").rglob("*.jpeg"))
    assert jpegs, "the pipeline must produce a JPEG in the sandboxed output folder"


def test_stop_mid_run_aborts_promptly_and_keeps_partial_output(open_page, tmp_path):
    page = open_page({})
    for i in range(80):
        Image.new("RGB", (800, 600), ((i * 7) % 256, 60, 120)).save(
            tmp_path / "input" / f"probe_{i:03d}.png"
        )

    page.click("#btn-start")
    page.wait_for_function("!document.getElementById('btn-stop').disabled")
    page.click("#btn-stop")
    clicked_at = time.monotonic()

    page.wait_for_function(
        """() => {
            const s = document.getElementById('run-status');
            return getComputedStyle(s).display !== 'none' && s.className.startsWith('status-');
        }""",
        timeout=15000,
    )
    reaction = time.monotonic() - clicked_at

    final = page.evaluate(
        """() => ({
            status_cls: document.getElementById('run-status').className,
            start_enabled: !document.getElementById('btn-start').disabled,
            stop_disabled: document.getElementById('btn-stop').disabled,
        })"""
    )
    assert final["status_cls"] == "status-aborted", \
        f"Stop must end the run as aborted, got {final}"
    assert final["start_enabled"], "Start must re-enable after an abort"
    assert final["stop_disabled"], "Stop must disarm after an abort"
    assert reaction < 5.0, f"abort took {reaction:.1f}s after the Stop click"

    jpegs = list((tmp_path / "output").rglob("*.jpg"))
    assert len(jpegs) < 80, "the abort must actually cut the run short"



def button_look(page, button_id="btn-start"):
    return page.evaluate(
        """id => {
            const b = document.getElementById(id);
            const s = getComputedStyle(b);
            return {
                disabled: b.disabled,
                visible: [s.opacity, s.backgroundColor, s.color],
                cursor: s.cursor,
                title: b.title,
            };
        }""",
        button_id,
    )


def test_clicking_start_immediately_changes_how_it_looks(open_page):
    page = open_page({})
    page.evaluate(
        """() => {
            const real = window.fetch;
            window.fetch = (url, opts) => url === '/api/process/start'
                ? new Promise(() => {})      // never settles
                : real(url, opts);
        }"""
    )

    before = button_look(page)
    assert before["disabled"] is False

    page.click("#btn-start")

    after = button_look(page)
    assert after["disabled"] is True, "Start was not disabled by the click"
    assert after["visible"] != before["visible"], (
        "Start is disabled but looks identical - nothing tells the user the "
        f"click registered (still {after['visible']})"
    )
    assert after["cursor"] == "not-allowed"
    assert after["title"], "a disabled Start should say why on hover"


def test_a_render_pass_cannot_hand_start_back_mid_run(open_page):
    page = open_page({})
    live = button_look(page)
    page.evaluate("() => { window.runActive = true; window.updateGlobalControls(); }")

    mid = button_look(page)
    assert mid["disabled"] is True, "a render pass re-enabled Start during a run"
    assert mid["visible"] != live["visible"], "a disabled Start still looks live"
    assert mid["cursor"] == "not-allowed"

    page.evaluate("() => { window.runActive = false; window.updateGlobalControls(); }")
    after = button_look(page)
    assert after["disabled"] is False, "Start never came back after the run"
    assert after["visible"] == live["visible"], "Start came back looking wrong"
    assert after["cursor"] == "pointer"
