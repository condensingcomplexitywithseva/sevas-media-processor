# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


PROBE = "console-pin-probe-7f3a"


def count_probe_lines(page, text):
    return page.evaluate(
        "t => Array.from(document.querySelectorAll('#console-output .log-line'))"
        ".filter(l => l.textContent.includes(t)).length",
        text,
    )


def wait_for_probe(page, text):
    page.wait_for_function(
        "t => Array.from(document.querySelectorAll('#console-output .log-line'))"
        ".some(l => l.textContent.includes(t))",
        arg=text,
        timeout=5000,
    )
    page.wait_for_timeout(700)


def test_ui_log_renders_exactly_once(open_page):
    page = open_page({})
    page.wait_for_timeout(400)
    page.evaluate(f"window.ui_logger.log('{PROBE}', 'UI', 'INFO')")
    wait_for_probe(page, PROBE)
    assert count_probe_lines(page, PROBE) == 1, \
        "a UI log line must appear exactly once (local render + SSE echo must not both land)"


def test_history_replay_after_reload_exactly_once(open_page):
    page = open_page({})
    page.wait_for_timeout(400)
    page.evaluate(f"window.ui_logger.log('{PROBE}', 'UI', 'INFO')")
    wait_for_probe(page, PROBE)

    page.reload()
    page.wait_for_selector("body.lang-loaded", timeout=15000)
    wait_for_probe(page, PROBE)
    assert count_probe_lines(page, PROBE) == 1, \
        "history replay must restore the UI line exactly once after a refresh"
