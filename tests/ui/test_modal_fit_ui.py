# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from pathlib import Path

LANGS = tuple(sorted(
    p.stem for p in (Path(__file__).resolve().parents[2] / "src" / "locales").glob("*.json")
))
APP_WIDTH = 1264
APP_HEIGHT = 800

DETAIL = (
    "[WinError 5] Access is denied: "
    r"D:\Media Archive\runs\OUTPUT FOLDER\current_run -> "
    r"D:\Media Archive\runs\OUTPUT FOLDER\old_current_run_2026-08-05_04-15-37"
)

SHOW_ARCHIVE_LOCKED_ALERT = """(detail) => {
    const message = window.getT('alert_config_err', 'Configuration Error')
        + '\\n\\n'
        + window.getT('err_archive_locked', 'err_archive_locked')
        + '\\n\\n(' + detail + ')';
    window.appAlert(message);
}"""

MEASURE = """() => {
    const dialog = document.getElementById('modal-dialog').getBoundingClientRect();
    const ok = document.getElementById('modal-ok').getBoundingClientRect();
    return {
        dialogTop: dialog.top, dialogBottom: dialog.bottom,
        okTop: ok.top, okBottom: ok.bottom,
        viewport: window.innerHeight,
        message: document.getElementById('modal-message').textContent,
    };
}"""


def test_archive_locked_alert_fits_the_window_in_every_language(open_page):
    page = open_page({})
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})

    too_tall = []
    for lang in LANGS:
        page.evaluate(f"changeLanguage('{lang}')")
        page.evaluate(SHOW_ARCHIVE_LOCKED_ALERT, DETAIL)
        page.wait_for_timeout(200)

        box = page.evaluate(MEASURE)
        assert "current_run" in box["message"], f"{lang}: wrong message under test"
        heading = page.evaluate("() => window.getT('alert_config_err', '')")
        assert heading and box["message"].startswith(heading + "\n\n"), (
            f"{lang}: the measured message does not start with the translated "
            "heading plus a blank line, so it is not composed the way "
            "app.js composes it"
        )

        if box["dialogTop"] < 0 or box["dialogBottom"] > box["viewport"]:
            too_tall.append(
                f"{lang}: dialog spans {box['dialogTop']:.0f}..{box['dialogBottom']:.0f} "
                f"in a {box['viewport']:.0f}px window"
            )
        if box["okTop"] < 0 or box["okBottom"] > box["viewport"]:
            too_tall.append(
                f"{lang}: OK button spans {box['okTop']:.0f}..{box['okBottom']:.0f} "
                f"in a {box['viewport']:.0f}px window"
            )

        page.click("#modal-ok")
        page.wait_for_timeout(150)

    assert not too_tall, "modal does not fit the real window:\n  " + "\n  ".join(too_tall)
