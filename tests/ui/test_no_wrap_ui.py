# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from pathlib import Path

LANGS = tuple(sorted(
    p.stem for p in (Path(__file__).resolve().parents[2] / "src" / "locales").glob("*.json")
))
APP_WIDTH = 1264
APP_HEIGHT = 800

AI_TOKEN_ERROR = {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai"}

AI_OFF_GARBAGE_RETRIES = {"ENABLE_LLM_INFERENCE": False, "LLM_MAX_RETRIES": "abc"}

ROWS_SINGLE_LINE = """
() => {
    const rows = document.querySelectorAll('#global-error-banner .banner-row');
    const out = [];
    for (const row of rows) {
        if (getComputedStyle(row).display === 'none') continue;
        const spans = row.querySelectorAll('span');
        const wrapped = row.getBoundingClientRect().height;
        spans.forEach(s => s.style.whiteSpace = 'nowrap');
        const single = row.getBoundingClientRect().height;
        spans.forEach(s => s.style.whiteSpace = '');
        out.push({
            label: row.id || row.textContent.trim().slice(0, 60),
            single_line: wrapped <= single + 1,
            text: row.textContent.trim().slice(0, 120),
        });
    }
    return out;
}
"""


def assert_banner_rows_single_line(page, scenario, expected_rows):
    for lang in LANGS:
        page.evaluate(f"changeLanguage('{lang}')")
        page.wait_for_timeout(300)
        rows = page.evaluate(ROWS_SINGLE_LINE)
        assert len(rows) == expected_rows, \
            f"{scenario} [{lang}]: expected {expected_rows} visible banner row(s), got {len(rows)}"
        for row in rows:
            assert row["single_line"], \
                f"{scenario} [{lang}]: banner row '{row['label']}' wraps past one line: {row['text']!r}"


def test_disk_error_banner_fits_on_one_line(open_page):
    page = open_page(AI_TOKEN_ERROR)
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    page.wait_for_timeout(200)
    assert_banner_rows_single_line(page, "disk errors", expected_rows=2)


def test_failed_apply_banner_fits_on_one_line(open_page):
    page = open_page(AI_OFF_GARBAGE_RETRIES,
                     tokens={"openai": "sk-fake0123456789abcdef0123456789abcdef"})
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    page.wait_for_timeout(200)

    page.select_option("#ENABLE_LLM_INFERENCE", "true")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    )
    unsaved_variant = page.evaluate("window.hasUnsavedEdits()")
    assert unsaved_variant, "sanity: this scenario must exercise the msg_resolve_errors wording"

    assert_banner_rows_single_line(page, "failed apply", expected_rows=2)
