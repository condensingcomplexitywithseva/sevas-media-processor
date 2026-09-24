# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0



import pytest


def _open_exports_tab(page):
    page.evaluate("window.switchTab('exports')")
    page.wait_for_timeout(400)


def _toast(page):
    return page.evaluate(
        """() => {
            const t = document.getElementById('generic-toast');
            return t ? { text: t.innerText, opacity: getComputedStyle(t).opacity,
                         background: getComputedStyle(t).backgroundColor } : null;
        }"""
    )


def _wait_for_toast(page):
    page.wait_for_function(
        "() => { const t = document.getElementById('generic-toast');"
        " return !!t && getComputedStyle(t).opacity === '1'; }"
    )
    return _toast(page)


def test_export_logs_button_writes_the_file_and_names_it_in_a_toast(open_page, tmp_path):
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


def test_logs_path_hint_shows_the_path_with_a_working_copy_button(open_page, tmp_path):
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

    page.locator('[data-notice-id="logs-folder"] button').click()
    toast = {"text": page.locator("#modal-message").inner_text()}
    assert "Could not open the folder" in toast["text"], toast["text"]
    assert page.locator('[data-notice-id="logs-folder"]').is_visible()


def test_export_logs_failure_persists_with_a_readable_reason(open_page):
    page = open_page({})
    _open_exports_tab(page)
    page.click("button:has(span[data-i18n='btn_export_log'])")
    page.wait_for_timeout(400)

    page.locator('[data-notice-id="logs"] button').click()
    toast = {"text": page.locator("#modal-message").inner_text()}
    assert "No system log found." in toast["text"], toast["text"]
    assert page.locator('[data-notice-id="logs"]').is_visible()

    assert not page.locator("#export-log-result").is_visible()


def test_failed_workbook_has_persistent_inventory_and_retry(open_page, tmp_path, monkeypatch):
    import data_exporter
    from test_api_routes import seed_export_run

    seed_export_run(tmp_path)
    original = data_exporter.SQLiteDataExporter._workbook

    def fail(*args, **kwargs):
        raise RuntimeError("Workbook save failed for this test")

    monkeypatch.setattr(data_exporter.SQLiteDataExporter, "_workbook", fail)
    page = open_page({})
    page.set_viewport_size({"width": 1280, "height": 850})
    _open_exports_tab(page)
    page.click("button:has(span[data-i18n='btn_export_db'])")
    page.wait_for_selector("#modal-overlay", state="visible")
    assert "Workbook save failed for this test" in page.locator("#export-db-files li").last.get_attribute("title")
    assert "could not be written" in page.locator("#modal-message").inner_text()
    assert page.locator("#modal-path").inner_text() == ""
    screenshot = tmp_path / "export-error-dialog-en.png"
    page.screenshot(path=str(screenshot))
    print(f"EXPORT_PREVIEW={screenshot}")
    page.click("#modal-ok")
    page.wait_for_timeout(3300)
    assert page.locator("#export-db-result").is_visible()
    assert page.locator("#export-db-files li").count() == 4
    assert page.locator("#export-run-notice").is_visible()
    assert page.locator("#export-db-actions").is_visible()
    assert page.locator("#export-db-status").inner_text() == "Not saved: Excel workbook (.xlsx)."
    assert page.locator("#export-db-saved").inner_text() == "Saved: CSV reports (3)"
    page.evaluate("window.renderErrors()")
    assert page.locator("#export-db-status").inner_text() == "Not saved: Excel workbook (.xlsx)."
    assert not page.locator("#export-db-location").is_visible()
    assert not page.locator("#export-db-files").is_visible()
    styles = page.evaluate("""() => {
        const status = getComputedStyle(document.getElementById('export-db-status'));
        const peer = getComputedStyle(document.querySelector('.error-text:not(#export-db-status)'));
        const reason = getComputedStyle(document.getElementById('export-db-reason'));
        const hint = getComputedStyle(document.querySelector('.hint:not(#export-db-reason)'));
        return [status.fontSize === peer.fontSize, status.fontWeight === peer.fontWeight,
                status.color === peer.color, reason.fontSize === hint.fontSize];
    }""")
    assert all(styles)
    screenshot = tmp_path / "export-recovery-en.png"
    page.screenshot(path=str(screenshot))
    print(f"EXPORT_PREVIEW={screenshot}")
    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(300)
    assert "Книга Excel (.xlsx)" in page.locator("#export-db-status").inner_text()
    assert "Повторить" in page.locator("[data-i18n='btn_retry_export']").inner_text()
    screenshot = tmp_path / "export-recovery-ru.png"
    page.screenshot(path=str(screenshot))
    print(f"EXPORT_PREVIEW={screenshot}")
    monkeypatch.setattr(data_exporter.SQLiteDataExporter, "_workbook", original)
    page.click("[data-i18n='btn_retry_export']")
    page.wait_for_function("window.exportOutcome && window.exportOutcome.status === 'success'")
    assert not page.locator("#export-run-notice").is_visible()
    assert page.locator("#export-db-files li").count() == 4
    assert "Не сохранено" not in page.locator("#export-db-files").inner_text()


def test_save_elsewhere_cancel_and_selection_preserve_settings(open_page, tmp_path):
    from test_api_routes import seed_export_run

    seed_export_run(tmp_path)
    page = open_page({})
    _open_exports_tab(page)
    page.click("button:has(span[data-i18n='btn_export_db'])")
    page.wait_for_function("window.exportOutcome && window.exportOutcome.status === 'success'")
    settings_bytes = (tmp_path / "settings.json").read_bytes()
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "/api/export/database" in r.url else None)
    page.evaluate("window.pywebview = {api: {browse_folder: () => Promise.resolve(null)}}")
    page.click("[data-i18n='btn_export_elsewhere']")
    page.wait_for_timeout(300)
    assert requests == []
    destination = tmp_path / "selected-folder"
    page.evaluate("path => { window.pywebview.api.browse_folder = () => Promise.resolve(path); }", str(destination))
    page.click("[data-i18n='btn_export_elsewhere']")
    page.wait_for_function("path => window.exportOutcome && window.exportOutcome.path === path", arg=str(destination))
    assert len(requests) == 1 and len(list(destination.iterdir())) == 4
    assert (tmp_path / "settings.json").read_bytes() == settings_bytes


def test_export_inventory_treats_untrusted_text_as_text(open_page):
    page = open_page({})
    _open_exports_tab(page)
    malicious = '<img src=x onerror="window.injected=true">'
    page.evaluate(
        "text => window.showExportOutcome({status:'error', "
        "saved:[{path:text,format:'csv',report:'Results'}], failed:[], notices:[]})",
        malicious,
    )
    assert page.locator("#export-db-files img").count() == 0
    page.locator("#export-db-details summary").click()
    assert malicious in page.locator("#export-db-files").inner_text()
    assert page.evaluate("window.injected === undefined")


def test_export_messages_identify_failed_formats_and_do_not_offer_an_unsaved_location(open_page):
    page = open_page({})
    _open_exports_tab(page)
    for language, expected_csv, expected_excel in (
        ("en", "Results report (.csv)", "Excel workbook (.xlsx)"),
        ("ru", "Отчёт с результатами (.csv)", "Книга Excel (.xlsx)"),
    ):
        page.evaluate("changeLanguage", language)
        for failures in (
            [{"report": "Results", "format": "csv", "category": "disk_full", "error": "disk full"}],
            [{"report": "Workbook", "format": "xlsx", "category": "locked", "error": "locked"}],
            [
                {"report": "Results", "format": "csv", "category": "disk_full", "error": "disk full"},
                {"report": "Workbook", "format": "xlsx", "category": "disk_full", "error": "disk full"},
            ],
        ):
            page.evaluate(
                "failed => window.showExportOutcome({status:'error', failed, saved:[], path:'unused'})", failures
            )
            message = page.locator("#export-db-status").inner_text()
            assert (expected_csv in message) == any(f["format"] == "csv" for f in failures)
            assert (expected_excel in message) == any(f["format"] == "xlsx" for f in failures)
            assert "unused" not in page.locator("#export-db-result").inner_text()
            page.locator("#export-db-details").evaluate("node => { node.open = true; }")
            assert not page.locator("#export-db-location").is_visible()
            assert page.locator("#export-db-saved").inner_text() in (
                "No report files were saved.",
                "Ни один отчёт не сохранён.",
            )
            page.locator("#export-db-details").evaluate("node => { node.open = false; }")


def test_workbook_limit_notice_remains_visible_without_opening_file_details(open_page):
    page = open_page({})
    _open_exports_tab(page)
    for format_name in ("csv", "xlsx"):
        page.evaluate(
            """format => window.showExportOutcome({status:'success',
            saved:[{report:'Workbook',format,path:'example.'+format}], failed:[],
            notices:['1 cells cut across the workbook']})""",
            format_name,
        )
        text = page.locator("#export-db-limits").inner_text()
        assert ("See Summary" in text) == (format_name == "xlsx")
        assert not page.locator("#export-db-files").is_visible()



def test_open_export_dialog_retranslates_its_domain_outcome(open_page):
    page = open_page({})
    page.route('**/api/export/database', lambda route: route.fulfill(json={
        'status': 'error', 'saved': [],
        'failed': [{'format': 'xlsx', 'report': 'Workbook', 'category': 'access_denied', 'error': 'denied'}],
    }))
    page.evaluate('exportDatabase()')
    page.wait_for_selector('#modal-message', state='visible')
    assert 'Excel workbook' in page.locator('#modal-message').inner_text()
    page.evaluate("changeLanguage('ru')")
    text = page.locator('#modal-message').inner_text()
    assert 'Книга Excel' in text and 'Нет доступа на запись' in text
    assert page.locator('#modal-path').inner_text() == ''



def test_missing_translation_and_incomplete_export_failure_stay_readable(open_page):
    from pathlib import Path
    page = open_page({})
    errors = []
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
    locales = Path(__file__).resolve().parents[2] / 'src/locales'
    for language in sorted(path.stem for path in locales.glob('*.json')):
        page.evaluate('changeLanguage', language)
        missing = page.evaluate("getT('export_error_permission')")
        assert isinstance(missing, str) and missing.strip()
        assert 'undefined' not in missing
        page.route('**/api/export/database', lambda route: route.fulfill(json={
            'status': 'error', 'saved': [],
            'failed': [{'format': 'csv', 'category': 'unrecognized'}],
        }))
        page.evaluate('exportDatabase()')
        page.wait_for_selector('#modal-overlay', state='visible')
        text = page.locator('#modal-message').inner_text()
        assert 'undefined' not in text and 'null' not in text
        assert page.locator('#export-db-reason').inner_text().strip()
        assert 'undefined' not in page.locator('#export-db-files').text_content()
        assert 'undefined' not in page.locator('#export-db-files li').last.get_attribute('title')
        page.click('#modal-ok')
        page.unroute('**/api/export/database')
    assert any('export_error_permission' in message for message in errors)



def test_export_precondition_preserves_output_setting_navigation(open_page, tmp_path):
    from pathlib import Path

    page = open_page({})
    before = (tmp_path / 'settings.json').read_bytes()
    for locale in sorted((Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json')):
        page.evaluate('changeLanguage', locale.stem)
        with page.expect_response('**/api/export/database') as response:
            page.evaluate('() => { exportDatabase(); }')
        assert response.value.status == 404
        page.wait_for_selector('#modal-overlay', state='visible')
        link = page.locator('#modal-message a[data-setting="OUTPUT_FOLDER_PATH"]')
        assert link.count() == 1, 'The no-results explanation directs the user to Output without a working link'
        draft = page.evaluate('JSON.stringify(window.draftState)')
        link.click()
        page.wait_for_timeout(400)
        assert page.locator('[name="OUTPUT_FOLDER_PATH"]').evaluate('(el) => el === document.activeElement')
        assert page.evaluate('JSON.stringify(window.draftState)') == draft
    assert (tmp_path / 'settings.json').read_bytes() == before


def test_save_elsewhere_picker_failure_persists_and_keeps_recovery(open_page, tmp_path):
    from test_api_routes import seed_export_run

    seed_export_run(tmp_path)
    page = open_page({})
    page.evaluate('() => { exportDatabase(); }')
    page.wait_for_function("exportOutcome && exportOutcome.status === 'success'")
    original = page.evaluate('JSON.stringify(exportOutcome)')
    disk = (tmp_path / 'settings.json').read_bytes()
    page.evaluate("""() => {
        window.pywebview = {api: {browse_folder: () => Promise.reject(new Error('Picker unavailable'))}};
        saveReportsElsewhere();
    }""")
    page.wait_for_selector('#modal-overlay', state='visible')
    assert 'Picker unavailable' in page.locator('#modal-detail').inner_text()
    page.click('#modal-ok')
    assert page.evaluate('JSON.stringify(exportOutcome)') == original
    assert (tmp_path / 'settings.json').read_bytes() == disk
    assert page.locator('[data-notice-id="export-picker"]').is_visible(), (
        'The failed folder picker needs a persistent recovery notice')



def _message_locales():
    from pathlib import Path
    return sorted(path.stem for path in (Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json'))


@pytest.mark.parametrize('condition', ['no_output', 'no_database', 'busy', 'missing_source'])
def test_export_refusal_has_its_own_recovery_without_artifact_claims(open_page, tmp_path, condition):
    import routes.execution_api as execution

    page = open_page({'OUTPUT_FOLDER_PATH': ''} if condition == 'no_output' else {})
    _open_exports_tab(page)
    disk = (tmp_path / 'settings.json').read_bytes()
    for language in _message_locales():
        page.evaluate('changeLanguage', language)
        draft = page.evaluate('JSON.stringify(draftState)')
        if condition == 'missing_source':
            page.evaluate("exportOutcome = {recovery_id: 'no-such-source'}")
        if condition == 'busy':
            assert execution.run_controller.lock.acquire(blocking=False)
        try:
            with page.expect_response('**/api/export/database') as response:
                page.evaluate('retry => { exportDatabase(retry); }', condition == 'missing_source')
        finally:
            if condition == 'busy':
                execution.run_controller.lock.release()
        body = response.value.json()
        assert body['export_state'] == 'refused'
        page.wait_for_selector('#modal-overlay', state='visible')
        message = page.locator('#modal-message').inner_text()
        assert page.evaluate("getT('export_partial')") not in message
        assert page.locator('#export-db-saved').inner_text() == ''
        assert not page.locator('#export-db-details').is_visible()
        assert not page.locator('#export-db-actions').is_visible()
        assert not page.locator('#export-db-location').is_visible()
        assert page.locator('#export-db-status').inner_text() == message
        if condition in ('no_output', 'no_database'):
            link = page.locator('#modal-message a[data-setting="OUTPUT_FOLDER_PATH"]')
            assert link.count() == 1
            link.click()
            page.wait_for_timeout(400)
            assert page.locator('[name="OUTPUT_FOLDER_PATH"]').evaluate('(el) => el === document.activeElement')
        else:
            page.click('#modal-ok')
        assert page.locator('[data-notice-id="export"]').is_visible()
        assert page.evaluate('JSON.stringify(draftState)') == draft
    assert (tmp_path / 'settings.json').read_bytes() == disk


def test_log_export_missing_output_has_live_navigation(open_page, tmp_path):
    page = open_page({'OUTPUT_FOLDER_PATH': ''})
    disk = (tmp_path / 'settings.json').read_bytes()
    for language in _message_locales():
        page.evaluate('changeLanguage', language)
        draft = page.evaluate('JSON.stringify(draftState)')
        with page.expect_response('**/api/export/logs'):
            page.evaluate('exportLogs()')
        page.locator('[data-notice-id="logs"] button').click()
        link = page.locator('#modal-message a[data-setting="OUTPUT_FOLDER_PATH"]')
        assert link.count() == 1
        link.click()
        page.wait_for_timeout(400)
        assert page.locator('[name="OUTPUT_FOLDER_PATH"]').evaluate('(el) => el === document.activeElement')
        assert page.evaluate('JSON.stringify(draftState)') == draft
    assert (tmp_path / 'settings.json').read_bytes() == disk


def test_export_composition_keeps_references_and_hostile_text(open_page):
    page = open_page({})
    text = '<img src=x onerror="window.injected=true"> {output}'
    page.route('**/api/export/database', lambda route: route.fulfill(status=404, json={
        'status': 'error', 'export_state': 'refused',
        'message': 'Choose {output}. Literal: {literal}',
        'refs': {'output': 'OUTPUT_FOLDER_PATH'}, 'args': {'literal': text},
    }))
    page.evaluate('exportDatabase()')
    page.wait_for_selector('#modal-overlay', state='visible')
    for language in _message_locales():
        page.evaluate('changeLanguage', language)
        for selector in ('#modal-message', '#export-db-status'):
            host = page.locator(selector)
            assert host.locator('a[data-setting="OUTPUT_FOLDER_PATH"]').count() == 1
            assert text in host.inner_text()
            assert host.locator('img').count() == 0
    assert page.evaluate('window.injected === undefined')
    def require_navigation():
        assert page.locator('#modal-message a[data-setting="OUTPUT_FOLDER_PATH"]').count() == 1
    require_navigation()
    page.locator('#modal-message').evaluate('(el) => { el.textContent = el.textContent; }')
    with pytest.raises(AssertionError):
        require_navigation()


@pytest.mark.parametrize('retry', [False, True])
def test_lost_export_response_does_not_claim_nothing_saved_or_retry_another_run(open_page, tmp_path, retry):
    from test_api_routes import seed_export_run

    seed_export_run(tmp_path)
    page = open_page({})
    _open_exports_tab(page)
    page.evaluate('exportDatabase()')
    original = page.evaluate('exportOutcome')
    assert original['status'] == 'success'
    destination = tmp_path / 'retry-destination'

    def lose_reply(route):
        reply = route.fetch()
        assert reply.status == 200
        route.abort()

    page.route('**/api/export/database', lose_reply)
    page.evaluate('data => exportDatabase(data.retry, data.retry ? data.path : null)',
                  {'retry': retry, 'path': str(destination)})
    page.wait_for_selector('#modal-overlay', state='visible')
    outcome = page.evaluate('exportOutcome')
    assert outcome['export_state'] == 'unknown'
    assert (outcome.get('recovery_id') == original['recovery_id']) == retry
    if retry:
        assert outcome['path'] == str(destination)
    assert list((destination if retry else tmp_path / 'output/exports').glob('*.xlsx'))
    for language in _message_locales():
        page.evaluate('changeLanguage', language)
        assert page.evaluate("getT('export_none_saved')") not in page.locator('#export-db-result').inner_text()
        assert page.locator('#export-db-saved').inner_text() == ''
        assert page.locator('#modal-detail').inner_text()
    page.click('#modal-ok')
    assert page.locator('[data-notice-id="export"]').is_visible()
    assert page.locator('#export-db-actions').is_visible() == retry


def test_logs_folder_recovery_keeps_path_without_position_words(open_page, tmp_path):
    (tmp_path / 'logs').mkdir()
    page = open_page({})
    for language in _message_locales():
        page.evaluate('changeLanguage', language)
        with page.expect_response('**/api/export/open_logs_folder'):
            page.evaluate('openLogsFolder()')
        page.locator('[data-notice-id="logs-folder"] button').click()
        text = page.locator('#modal-message').inner_text().lower()
        assert all(word not in text for word in ('above', 'below', 'выше', 'ниже'))
        assert page.locator('#modal-path').inner_text() == str(tmp_path / 'logs')
        assert page.locator('#modal-path-copy').is_visible()
        page.click('#modal-ok')



def test_busy_retry_keeps_the_known_source_and_chosen_destination(open_page, tmp_path):
    from pathlib import Path
    from test_api_routes import seed_export_run
    import routes.execution_api as execution

    seed_export_run(tmp_path)
    page = open_page({})
    _open_exports_tab(page)
    page.evaluate('exportDatabase()')
    original = page.evaluate('exportOutcome')
    destination = str(tmp_path / 'chosen-retry-folder')
    assert execution.run_controller.lock.acquire(blocking=False)
    try:
        page.evaluate('path => exportDatabase(true, path)', destination)
    finally:
        execution.run_controller.lock.release()
    page.wait_for_selector('#modal-overlay', state='visible')
    refusal = page.evaluate('exportOutcome')
    assert refusal['export_state'] == 'refused'
    assert refusal.get('recovery_id') == original['recovery_id']
    assert refusal.get('path') == destination
    assert page.locator('#export-db-saved').inner_text() == ''
    page.click('#modal-ok')
    page.evaluate('exportDatabase(true)')
    completed = page.evaluate('exportOutcome')
    assert completed['status'] == 'success'
    assert completed['recovery_id'] == original['recovery_id']
    assert all(Path(file['path']).parent == Path(destination) for file in completed['saved'])


def test_delayed_automatic_result_cannot_replace_newer_manual_export(open_page, tmp_path):
    import central_logger
    from test_api_routes import seed_export_run

    seed_export_run(tmp_path)
    page = open_page({})
    page.evaluate('() => { exportDatabase(); }')
    page.wait_for_function("exportOutcome && exportOutcome.status === 'success'")
    older = page.evaluate('JSON.parse(JSON.stringify(exportOutcome))')
    destination = str(tmp_path / 'new destination')
    page.evaluate('destination => { exportDatabase(false, destination); }', destination)
    page.wait_for_function("destination => exportOutcome.status === 'success' && exportOutcome.path === destination",
                           arg=destination)
    newer = page.evaluate('JSON.stringify(exportOutcome)')
    central_logger.global_broadcaster.emit(dict(older, type='export_result'))
    central_logger.global_broadcaster.emit(
        {'type':'log', 'content':'delivery-barrier', 'category':'TEST', 'level':'INFO'})
    page.wait_for_function("document.getElementById('console-output').textContent.includes('delivery-barrier')")
    assert page.evaluate('JSON.stringify(exportOutcome)') == newer


@pytest.mark.parametrize('delivery', ['during', 'after'])
def test_old_event_cannot_replace_unconfirmed_new_export(open_page, tmp_path, delivery):
    from pathlib import Path
    import central_logger
    from test_api_routes import seed_export_run

    seed_export_run(tmp_path)
    page = open_page({})
    page.evaluate('exportDatabase()')
    old = page.evaluate('JSON.parse(JSON.stringify(exportOutcome))')
    assert old['status'] == 'success'
    destination = str(tmp_path / 'new export destination')

    def lose_reply(route):
        reply = route.fetch()
        assert reply.status == 200
        if delivery == 'during':
            page.evaluate('data => receiveAutomaticExport(data)', old)
        route.abort()

    page.route('**/api/export/database', lose_reply)
    page.evaluate('destination => exportDatabase(false, destination)', destination)
    page.wait_for_selector('#modal-overlay', state='visible')
    assert list(Path(destination).glob('*.xlsx'))
    before = page.evaluate('JSON.stringify(exportOutcome)')
    assert page.evaluate('exportOutcome.export_state') == 'unknown'
    if delivery == 'after':
        central_logger.global_broadcaster.emit(dict(old, type='export_result'))
        central_logger.global_broadcaster.emit(
            {'type': 'log', 'content': 'review-delivery-barrier', 'category': 'TEST', 'level': 'INFO'})
        page.wait_for_function("document.getElementById('console-output').textContent.includes('review-delivery-barrier')")
    assert page.evaluate('JSON.stringify(exportOutcome)') == before
    assert page.locator('[data-notice-id="export"]').is_visible()


@pytest.mark.parametrize('next_action', ['manual', 'run', 'refused_start'])
def test_unseen_old_export_stays_blocked_until_a_confirmed_new_operation(open_page, tmp_path, next_action):
    from test_api_routes import seed_export_run

    seed_export_run(tmp_path)
    page = open_page({})
    response = page.request.post(page.url + 'api/export/database',
                                 headers={'X-App-Token': page.evaluate('window.API_TOKEN')})
    assert response.ok
    unseen = response.json()
    assert page.evaluate('exportOutcome') is None

    def lose_reply(route):
        reply = route.fetch()
        assert reply.ok
        route.abort()
    page.route('**/api/export/database', lose_reply)
    page.evaluate('() => { exportDatabase(); }')
    page.wait_for_selector('#modal-overlay', state='visible')
    page.click('#modal-ok')
    page.evaluate('data => receiveAutomaticExport(data)', unseen)
    assert page.evaluate('exportOutcome.export_state') == 'unknown'
    page.evaluate("changeLanguage('ru')")
    assert page.locator('[data-notice-id="export"]').is_visible()
    assert page.evaluate('exportOutcome.export_state') == 'unknown'
    page.unroute('**/api/export/database')

    if next_action == 'manual':
        page.evaluate('() => { exportDatabase(); }')
        page.wait_for_function("exportOutcome.status === 'success'")
        assert page.evaluate('exportOutcome.export_revision') > unseen['export_revision']
    elif next_action == 'run':
        with page.expect_response('**/api/process/start') as started:
            page.evaluate('() => { startProcessing(); }')
        start = started.value.json()
        assert start['status'] == 'success'
        page.wait_for_function("exportOutcome.status === 'success' && exportOutcome.type === 'export_result'")
        assert page.evaluate('exportOutcome.export_revision') > start['export_barrier'] > unseen['export_revision']
    else:
        page.route('**/api/process/start', lambda route: route.fulfill(status=400, json={
            'status':'error', 'message_key':'err_run_active'}))
        page.evaluate('() => { startProcessing(); }')
        page.wait_for_selector('#modal-overlay', state='visible')
        page.click('#modal-ok')
        page.evaluate('data => receiveAutomaticExport(data)', unseen)
        assert page.evaluate('exportOutcome.export_state') == 'unknown'
