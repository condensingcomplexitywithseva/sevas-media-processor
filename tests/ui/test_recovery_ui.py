# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

BROKEN_RAW = '{"JPEG_QUALITY": 85,,, broken'
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def real_settings_guard():
    real = REPO_ROOT / "settings.json"
    before = real.read_bytes() if real.exists() else None
    yield
    if before is None:
        intact = not real.exists()
    else:
        intact = real.exists() and real.read_bytes() == before
    if not intact:
        strays = sorted(REPO_ROOT.glob("settings_corrupted_backup_*.json"))
        if before is not None:
            real.write_bytes(before)
        elif real.exists():
            real.unlink()
        for stray in strays:
            stray.unlink()
        pytest.fail(
            "ISOLATION BREACH: the Reset click touched the repo-root "
            "settings.json (restored from the in-memory copy). "
            "/api/settings/reset must resolve get_settings_path()."
        )


def test_broken_json_shows_recovery_screen(open_page, tmp_path):
    page = open_page({}, raw_settings=BROKEN_RAW)
    page.wait_for_timeout(300)

    state = page.evaluate(
        """() => {
            const fatal = document.getElementById('general-fatal-error');
            const instr = document.getElementById('fatal-corrupted-instructions');
            const path = document.getElementById('corrupted-settings-path');
            return {
                fatal_visible: !!fatal && getComputedStyle(fatal).display !== 'none',
                instructions_visible: !!instr && getComputedStyle(instr).display !== 'none',
                text: instr ? instr.textContent : '',
                shown_path: path ? path.textContent.trim() : '',
            };
        }"""
    )
    assert state["fatal_visible"], "fatal banner must show for unparseable settings.json"
    assert state["instructions_visible"], "both recovery options must be offered"
    assert "Reset" in state["text"] and "settings.json" in state["text"]
    assert state["shown_path"] == str(tmp_path / "settings.json")


def test_reset_button_backs_up_and_writes_defaults(open_page, tmp_path,
                                                   real_settings_guard):
    page = open_page({}, raw_settings=BROKEN_RAW)
    page.wait_for_timeout(300)

    page.click('button:has(span[data-i18n="btn_reset_defaults"])')
    page.wait_for_function(
        """() => {
            const done = document.getElementById('fatal-reset-instructions');
            const old = document.getElementById('fatal-corrupted-instructions');
            return getComputedStyle(done).display !== 'none'
                && getComputedStyle(old).display === 'none';
        }"""
    )

    backups = sorted(tmp_path.glob("settings_corrupted_backup_*.json"))
    assert len(backups) == 1, f"expected exactly one backup, got {backups}"
    assert backups[0].read_text(encoding="utf-8") == BROKEN_RAW, \
        "the backup must preserve the corrupted content byte-for-byte"

    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["JPEG_QUALITY"] == 90, "defaults must land in settings.json"

    shown_path = page.evaluate(
        "document.getElementById('backup-path-display').innerText"
    )
    assert shown_path == str(backups[0]), \
        "the on-screen backup path must point at the real backup file"


def test_apply_cannot_overwrite_the_corrupted_file(open_page, tmp_path):
    page = open_page({}, raw_settings=BROKEN_RAW)
    page.wait_for_timeout(300)

    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)
    page.fill('input[name="JPEG_QUALITY"]', "55")
    page.wait_for_timeout(200)
    assert page.evaluate("!document.getElementById('btn-apply').disabled"), \
        "one edit must arm Apply - if it no longer does, this test stopped " \
        "exercising the dangerous path and needs rewriting, not deleting"

    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    )

    assert (tmp_path / "settings.json").read_text(encoding="utf-8") == BROKEN_RAW, \
        "Apply overwrote the corrupted file the user was told to fix by hand"

    still_offered = page.evaluate(
        """() => {
            const instr = document.getElementById('fatal-corrupted-instructions');
            return !!instr && getComputedStyle(instr).display !== 'none';
        }"""
    )
    assert still_offered, "the refusal must leave the recovery options on screen"

def console_lines(page):
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('#console-output .log-line'))
                      .map(l => l.textContent)"""
    )


def apply_an_edit(page, value):
    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)
    page.fill('input[name="JPEG_QUALITY"]', value)
    page.wait_for_timeout(200)
    page.click("#btn-apply")


def test_refused_save_says_the_file_is_unreadable_not_invalid(open_page):
    page = open_page({}, raw_settings=BROKEN_RAW)
    page.wait_for_timeout(400)

    apply_an_edit(page, "55")
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('#console-output .log-line'))
                      .some(l => l.textContent.includes('REFUSED'))""",
        timeout=5000,
    )
    page.wait_for_timeout(500)

    lines = [line for line in console_lines(page) if "REFUSED" in line]
    assert len(lines) == 1, f"expected exactly one refusal line, got {lines}"
    assert "cannot be read" in lines[0]
    assert "nothing was written" in lines[0]
    assert not any("validation errors" in line for line in console_lines(page)), \
        "a refusal must not be reported as a validation failure"


def test_a_real_validation_failure_still_says_validation(open_page):
    page = open_page({})
    page.wait_for_timeout(400)

    apply_an_edit(page, "500")
    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('#console-output .log-line'))
                      .some(l => l.textContent.includes('validation errors'))""",
        timeout=5000,
    )
    page.wait_for_timeout(500)

    assert not any("REFUSED" in line for line in console_lines(page)), \
        "a validation failure must not be reported as a refusal"


def error_toast(page):
    return page.evaluate(
        """() => {
            const t = document.getElementById('global-error-banner');
            return { visible: !!t && getComputedStyle(t).display !== 'none',
                     text: t ? t.textContent.trim() : '' };
        }"""
    )


def test_refused_save_notice_names_the_unreadable_file(open_page):
    page = open_page({}, raw_settings=BROKEN_RAW)
    page.wait_for_timeout(400)

    apply_an_edit(page, "55")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'",
        timeout=5000,
    )

    toast = error_toast(page)
    assert "cannot be read" in toast["text"], toast
    assert "validation" not in toast["text"].lower(), \
        "a refusal must not be toasted as a validation failure"


def test_a_real_validation_failure_keeps_the_field_error_summary(open_page):
    page = open_page({})
    page.wait_for_timeout(400)

    apply_an_edit(page, "500")
    page.wait_for_function(
        "() => getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'",
        timeout=5000,
    )

    toast = error_toast(page)
    assert "Not saved. Errors" in toast["text"], toast
    assert "cannot be read" not in toast["text"], \
        "a validation failure must not be toasted as a refusal"


def test_a_missing_editor_tells_the_user_where_the_file_is(open_page, tmp_path,
                                                           monkeypatch,
                                                           real_settings_guard):
    import routes.settings_api as settings_api

    def no_notepad(*args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(settings_api.subprocess, "Popen", no_notepad)

    page = open_page({}, raw_settings=BROKEN_RAW)
    page.wait_for_timeout(300)

    page.evaluate("() => { window.openSettingsFile('active'); }")
    page.wait_for_function(
        """() => {
            const o = document.getElementById('modal-overlay');
            return o && getComputedStyle(o).display !== 'none';
        }""",
        timeout=5000,
    )

    state = page.evaluate(
        """() => {
            const path = document.getElementById('modal-path');
            const copy = document.getElementById('modal-path-copy');
            return {
                message: document.getElementById('modal-message').textContent,
                path: path.textContent,
                path_visible: getComputedStyle(path).display !== 'none',
                path_mono: getComputedStyle(path).fontFamily.toLowerCase(),
                copy_visible: getComputedStyle(copy).display !== 'none',
                cancel_hidden: getComputedStyle(
                    document.getElementById('modal-cancel')).display === 'none',
            };
        }"""
    )
    assert state["path"] == str(tmp_path / "settings.json")
    assert state["path_visible"] and state["copy_visible"]
    assert "monospace" in state["path_mono"], state["path_mono"]
    assert "{path}" not in state["message"], "the placeholder leaked into the text"
    assert str(tmp_path) not in state["message"], "the path is shown twice"
    assert state["cancel_hidden"], "an alert offers OK only, not a choice"


def test_an_ordinary_dialog_shows_no_path_block(open_page):
    page = open_page({})
    page.wait_for_timeout(400)

    page.evaluate("() => { window.appConfirm('Plain question?'); }")
    page.wait_for_function(
        """() => { const o = document.getElementById('modal-overlay');
                   return o && getComputedStyle(o).display !== 'none'; }""",
        timeout=5000,
    )

    state = page.evaluate(
        """() => ({
            path_visible: getComputedStyle(
                document.getElementById('modal-path')).display !== 'none',
            copy_visible: getComputedStyle(
                document.getElementById('modal-path-copy')).display !== 'none',
            widened: document.getElementById('modal-dialog')
                             .classList.contains('with-path'),
        })"""
    )
    assert not state["path_visible"]
    assert not state["copy_visible"]
    assert not state["widened"]



@pytest.mark.parametrize('operation,endpoint', [
    ('openSettingsFile("active")', '**/api/settings/open_file'),
    ('resetSettings()', '**/api/settings/reset'),
    ('commitGlobalDraft()', '**/api/settings/commit'),
    ('openLogsFolder()', '**/api/export/open_logs_folder'),
    ('exportLogs()', '**/api/export/logs'),
    ('openExternalLink("github")', '**/api/about/open_link'),
])
@pytest.mark.parametrize('failure', ['disconnect', 'invalid_json'])
def test_operation_transport_failures_leave_a_persistent_notice(open_page, tmp_path, operation, endpoint, failure):
    page = open_page({})
    before = (tmp_path / 'settings.json').read_bytes()
    if failure == 'disconnect':
        page.route(endpoint, lambda route: route.abort('failed'))
    else:
        page.route(endpoint, lambda route: route.fulfill(status=502, content_type='text/html', body='gateway failed'))
    with page.expect_request(endpoint):
        page.evaluate('() => { window.' + operation + '; }')
    page.wait_for_timeout(400)
    if page.locator('#modal-overlay').is_visible():
        page.click('#modal-ok')
    page.evaluate('renderErrors()')
    notices = page.locator('#persistent-notices [data-notice-id]')
    assert notices.count() == 1, f'{operation} lost its failure after the dialog was dismissed'
    assert notices.first.is_visible()
    page.evaluate("changeLanguage('ru')")
    assert notices.count() == 1
    assert (tmp_path / 'settings.json').read_bytes() == before



@pytest.mark.parametrize('backup_exists', [False, True])
def test_backup_notice_only_offers_an_actionable_file(open_page, tmp_path, monkeypatch, backup_exists):
    import routes.settings_api as settings_api

    backup = tmp_path / 'settings_corrupted_backup_20260923_120000.json'
    if backup_exists:
        backup.write_text('{broken', encoding='utf-8')
    def refuse_editor(*args, **kwargs):
        raise OSError('Editor unavailable')
    monkeypatch.setattr(settings_api.subprocess, 'Popen', refuse_editor)
    page = open_page({})
    disk = (tmp_path / 'settings.json').read_bytes()
    for locale in sorted((REPO_ROOT / 'src/locales').glob('*.json')):
        page.evaluate('changeLanguage', locale.stem)
        with page.expect_response('**/api/settings/open_file') as response:
            page.evaluate("openSettingsFile('backup')")
        page.wait_for_selector('#modal-overlay', state='visible')
        assert response.value.json()['path'] == (str(backup) if backup_exists else '')
        assert page.locator('#modal-path-copy').is_visible() == backup_exists
        assert page.locator('#modal-path').inner_text() == (str(backup) if backup_exists else '')
        assert '{path}' not in page.locator('#modal-message').inner_text()
        page.click('#modal-ok')
    assert (tmp_path / 'settings.json').read_bytes() == disk


@pytest.mark.parametrize('token_state', ['absent', 'saved', 'wiped'])
def test_token_instructions_are_truthful_before_save_and_after_wipe(open_page, tmp_path, token_state):
    import config_loader

    tokens = None if token_state == 'absent' else {'openai': 'sk-fake0123456789abcdef0123456789abcdef'}
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'openai'}, tokens=tokens)
    if token_state == 'wiped':
        page.evaluate("() => { wipeToken('openai'); }")
        with page.expect_response('**/api/settings/wipe_token'):
            page.click('#modal-ok')
        page.wait_for_function('!rawOriginalState.ENV_TOKENS.openai')
    assert bool(config_loader._manager.token_manager.get_tokens().get('OPENAI_TOKEN')) == (token_state == 'saved')
    if token_state == 'absent':
        assert not (tmp_path / '.env').exists()
    for locale in sorted((REPO_ROOT / 'src/locales').glob('*.json')):
        page.evaluate('changeLanguage', locale.stem)
        hints = page.locator('[data-i18n="hint_prov_token"]').all_text_contents()
        assert hints
        for hint in hints:
            assert str(tmp_path) not in hint and '.env' not in hint
            assert 'securely' not in hint and 'безопасно' not in hint
            instruction = {'en': 'save', 'ru': 'сохран'}.get(locale.stem)
            if instruction:
                assert instruction in hint.lower()
        assert page.locator('#prov_token_openai').get_attribute('placeholder') == (
            '********' if token_state == 'saved' else page.evaluate("getT('placeholder_token')"))


def test_lost_reset_reply_reports_uncertainty_after_real_reset(open_page, tmp_path):
    page = open_page({'MAX_DIMENSION': 789})
    before = (tmp_path / 'settings.json').read_bytes()
    def lose_reply(route):
        response = route.fetch()
        assert response.ok
        route.abort('failed')
    page.route('**/api/settings/reset', lose_reply)
    page.evaluate('resetSettings()')
    page.wait_for_selector('#modal-overlay', state='visible')
    assert (tmp_path / 'settings.json').read_bytes() != before
    assert page.locator('#modal-message').inner_text() == page.evaluate("getT('err_settings_reset_unconfirmed')")
    page.click('#modal-ok')
    assert page.locator('[data-notice-id="reset"]').is_visible()
    page.evaluate("notice({id:'unrelated', key:'notice_action_failed'})")
    page.unroute('**/api/settings/reset')
    page.evaluate('resetSettings()')
    page.wait_for_function("!window.notices.has('reset')")
    assert page.locator('[data-notice-id="unrelated"]').is_visible()


@pytest.mark.parametrize('operation,endpoint,confirmation', [
    ('startProcessing()', '**/api/process/start', False),
    ('stopProcessing()', '**/api/process/stop', False),
    ('wipeToken("openai")', '**/api/settings/wipe_token', True),
    ('clearLogs()', '**/api/export/clear_logs', True),
])
@pytest.mark.parametrize('failure', ['disconnect', 'invalid_json'])
def test_run_and_confirmation_transport_notices(
        open_page, tmp_path, monkeypatch, operation, endpoint, confirmation, failure):
    import threading
    from routes import execution_api
    release = threading.Event()
    if operation == 'stopProcessing()':
        class HeldCore:
            def run(self):
                release.wait(15)
        monkeypatch.setattr(execution_api, 'ProcessorCore', lambda *args, **kwargs: HeldCore())
    page = open_page({})
    before = (tmp_path / 'settings.json').read_bytes()
    try:
        if operation == 'stopProcessing()':
            page.click('#btn-start')
            page.wait_for_function("window.runState.phase === 'running'")
        if failure == 'disconnect':
            page.route(endpoint, lambda route: route.abort('failed'))
        else:
            page.route(endpoint, lambda route: route.fulfill(status=502, content_type='text/html', body='unreadable'))
        page.evaluate('() => { ' + operation + '; }')
        if confirmation:
            page.locator('#modal-ok').click()
        page.wait_for_selector('[data-notice-id]')
        if page.locator('#modal-overlay').is_visible():
            page.locator('#modal-ok').click()
        page.evaluate('renderErrors()')
        assert page.locator('[data-notice-id]').count() == 1
        assert (tmp_path / 'settings.json').read_bytes() == before
    finally:
        release.set()
        worker = execution_api.run_controller.thread
        if worker is not None:
            worker.join(5)
            assert not worker.is_alive()
