# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

PROBE = "token-handoff-probe-4c19"


def api_calls(page):
    seen = []
    page.on(
        "response",
        lambda r: seen.append((r.url.split("/api/")[-1].split("?")[0], r.status))
        if "/api/" in r.url
        else None,
    )
    return seen


def prove_the_pipe_is_live(page, probe=PROBE):
    page.evaluate(f"window.ui_logger.log('{probe}', 'UI', 'INFO')")
    try:
        page.wait_for_function(
            "t => Array.from(document.querySelectorAll('#console-output .log-line'))"
            ".some(l => l.textContent.includes(t))",
            arg=probe,
            timeout=5000,
        )
        return True
    except Exception:
        return False


def test_the_served_page_contains_no_token(open_page):
    from routes.web_server import SESSION_TOKEN

    page = open_page({})
    served_html = page.evaluate(
        "async () => (await fetch('/', {cache: 'no-store'})).text()"
    )
    assert SESSION_TOKEN not in served_html
    assert "__receiveApiToken" in served_html


def test_the_token_is_not_left_lying_in_the_rendered_page_either(open_page):
    from routes.web_server import SESSION_TOKEN

    page = open_page({})
    prove_the_pipe_is_live(page)
    assert SESSION_TOKEN not in page.content()


def test_the_app_works_once_the_token_is_delivered(open_page):
    page = open_page({})
    calls = api_calls(page)
    page.wait_for_timeout(400)

    assert prove_the_pipe_is_live(page), "the console pipe never came up"
    assert not [c for c in calls if c[1] == 403], f"calls were refused: {calls}"


def test_calls_wait_for_the_token_instead_of_being_refused(open_page):
    page = open_page({}, deliver_token=False)
    calls = api_calls(page)
    page.wait_for_timeout(2000)

    assert not calls, (
        "the page made /api calls before the token arrived; without the wait "
        f"these are refused and the console panel dies silently: {calls}"
    )
    assert page.evaluate("() => window.API_TOKEN") is None
    console_text = page.evaluate(
        "() => document.getElementById('console-output').innerText"
    )
    assert not console_text.strip(), "console populated without a token?"


def test_a_late_token_still_unblocks_the_queued_calls(open_page):
    from routes.web_server import SESSION_TOKEN

    page = open_page({}, deliver_token=False)
    calls = api_calls(page)
    page.wait_for_timeout(1200)
    assert not calls

    page.evaluate("token => window.__receiveApiToken(token)", SESSION_TOKEN)
    page.wait_for_timeout(600)

    assert calls, "the queued calls never fired after the token arrived"
    assert not [c for c in calls if c[1] == 403], calls
    assert prove_the_pipe_is_live(page), (
        "the SSE stream never attached even after the token arrived"
    )


def test_a_reloaded_page_waits_for_a_fresh_token(open_page):
    page = open_page({}, deliver_token=False)
    page.reload()
    page.wait_for_selector("body.lang-loaded", timeout=15000)

    assert page.evaluate("() => window.API_TOKEN") is None
    assert page.evaluate("() => typeof window.__receiveApiToken") == "function"


def test_the_app_still_works_after_a_refresh(open_page):
    page = open_page({})
    page.wait_for_timeout(400)
    assert prove_the_pipe_is_live(page)

    page.reload()
    page.wait_for_selector("body.lang-loaded", timeout=15000)
    page.wait_for_timeout(400)

    assert page.evaluate("() => window.API_TOKEN") is not None, (
        "the refreshed window never received a token - F5 kills the app"
    )
    assert prove_the_pipe_is_live(page, PROBE + "-after-reload")
