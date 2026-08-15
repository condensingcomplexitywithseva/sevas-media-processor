# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0



def _open_exports_tab(page):
    page.evaluate("window.switchTab('exports')")
    page.wait_for_timeout(400)


def _toast(page):
    return page.evaluate(
        """() => {
            const t = document.getElementById('generic-toast');
            return t ? { text: t.innerText, opacity: getComputedStyle(t).opacity,
                         background: t.style.background } : null;
        }"""
    )


def _wait_for_toast(page):
    page.wait_for_function(
        "() => { const t = document.getElementById('generic-toast');"
        " return !!t && getComputedStyle(t).opacity === '1'; }"
    )
    return _toast(page)


def test_export_logs_button_writes_the_file_and_names_it_in_a_toast(
    open_page, tmp_path
):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    log_file = logs_dir / "system_log_2026-07-05.txt"
    log_file.write_text("session lines\n", encoding="utf-8")

    page = open_page({})
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    _open_exports_tab(page)

    for row in ("#export-log-result", "#export-db-result"):
        assert page.locator(row).evaluate("el => el.style.display") == "none"

    page.click("button:has(span[data-i18n='btn_export_log'])")
    page.wait_for_timeout(400)

    exported = tmp_path / "output" / "exports" / log_file.name
    assert exported.exists(), "the export button did not produce the file"
    assert exported.read_text(encoding="utf-8") == "session lines\n"

    toast = _wait_for_toast(page)
    assert str(exported) in toast["text"], toast["text"]
    assert "error-color" not in toast["background"]

    assert page.locator("#export-log-result").is_visible()
    assert page.locator("#export-log-path").inner_text() == str(exported)
    page.click("#export-log-path + button")
    page.wait_for_timeout(300)
    clip = page.evaluate("navigator.clipboard.readText()")
    assert clip == str(exported), "clipboard does not carry the exported path"


def test_logs_path_hint_shows_the_path_with_a_working_copy_button(
    open_page, tmp_path
):
    page = open_page({})
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    _open_exports_tab(page)

    shown = page.locator("#logs-path-display").inner_text()
    assert shown == str(tmp_path / "logs"), shown

    page.click("#logs-path-display + button")
    page.wait_for_timeout(300)
    clip = page.evaluate("navigator.clipboard.readText()")
    assert clip == shown, "clipboard does not carry the logs path"


def test_all_logs_button_reports_an_open_failure_honestly(open_page, tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "system_log_2026-07-05.txt").write_text("lines", encoding="utf-8")

    page = open_page({})
    _open_exports_tab(page)
    page.click("button:has(span[data-i18n='btn_all_logs_folder'])")
    page.wait_for_timeout(400)

    toast = _wait_for_toast(page)
    assert "Could not open the folder" in toast["text"], toast["text"]
    assert "error-color" in toast["background"]


def test_export_logs_failure_surfaces_as_a_red_toast(open_page):
    page = open_page({})
    _open_exports_tab(page)
    page.click("button:has(span[data-i18n='btn_export_log'])")
    page.wait_for_timeout(400)

    toast = _wait_for_toast(page)
    assert "No system log found." in toast["text"], toast["text"]
    assert "error-color" in toast["background"]

    assert not page.locator("#export-log-result").is_visible()
