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
        "getComputedStyle(document.getElementById('error-toast')).opacity === '1'"
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

    lines = [l for l in console_lines(page) if "REFUSED" in l]
    assert len(lines) == 1, f"expected exactly one refusal line, got {lines}"
    assert "cannot be read" in lines[0]
    assert "nothing was written" in lines[0]
    assert not any("validation errors" in l for l in console_lines(page)), \
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

    assert not any("REFUSED" in l for l in console_lines(page)), \
        "a validation failure must not be reported as a refusal"


def error_toast(page):
    return page.evaluate(
        """() => {
            const t = document.getElementById('error-toast');
            return { visible: !!t && t.style.opacity === '1',
                     text: t ? t.textContent.trim() : '' };
        }"""
    )


def test_refused_save_toast_names_the_unreadable_file(open_page):
    page = open_page({}, raw_settings=BROKEN_RAW)
    page.wait_for_timeout(400)

    apply_an_edit(page, "55")
    page.wait_for_function(
        "() => document.getElementById('error-toast').style.opacity === '1'",
        timeout=5000,
    )

    toast = error_toast(page)
    assert "cannot be read" in toast["text"], toast
    assert "validation" not in toast["text"].lower(), \
        "a refusal must not be toasted as a validation failure"


def test_a_real_validation_failure_keeps_the_validation_toast(open_page):
    page = open_page({})
    page.wait_for_timeout(400)

    apply_an_edit(page, "500")
    page.wait_for_function(
        "() => document.getElementById('error-toast').style.opacity === '1'",
        timeout=5000,
    )

    toast = error_toast(page)
    assert "validation errors" in toast["text"], toast
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
