# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


FLASH_RGB = "211, 47, 47"

APP_WIDTH = 1264
APP_HEIGHT = 800

SAMPLE_PULSE = """
(fieldId) => {
    const group = document.getElementById(fieldId).closest('.form-group');
    const samples = [];
    return new Promise(resolve => {
        const t0 = performance.now();
        const timer = setInterval(() => {
            samples.push({
                flash_class: group.classList.contains('error-flash'),
                box_shadow: getComputedStyle(group).boxShadow,
            });
            if (performance.now() - t0 > 1200) { clearInterval(timer); resolve(samples); }
        }, 100);
    });
}
"""


import pytest

def read_navigation(page, field_id):
    return page.evaluate(
        """(fieldId) => {
        const activeTab = document.querySelector('.sidebar-tab.active');
        const field = document.getElementById(fieldId);
        const rect = field.getBoundingClientRect();
        return {
            active_tab: activeTab ? activeTab.getAttribute('data-tab') : null,
            field_visible: field.offsetParent !== null,
            field_in_viewport: rect.top >= 0 && rect.bottom <= window.innerHeight,
            value: field.value,
        };
    }""",
        field_id,
    )


def assert_pulse_visible(samples):
    assert any(s["flash_class"] for s in samples), "error-flash never applied"
    assert any(FLASH_RGB in s["box_shadow"] for s in samples), (
        "the pulse ring never RENDERED (class toggled but box-shadow "
        f"stayed overridden): {samples}"
    )


def test_jump_navigates_and_pulses_on_disk_error(open_page):
    page = open_page({"DOCUMENT_RANGE": "abc"})
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    page.wait_for_timeout(200)
    page.click("#global-error-text .banner-jump-btn")
    page.wait_for_timeout(100)

    samples = page.evaluate(SAMPLE_PULSE, "document_range")
    assert_pulse_visible(samples)

    nav = read_navigation(page, "document_range")
    assert nav["active_tab"] == "docs"
    assert nav["field_visible"]
    assert nav["field_in_viewport"]


def test_jump_pulse_is_visible_on_just_edited_field(open_page):
    page = open_page({})
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    page.wait_for_timeout(200)
    page.evaluate("switchTab('docs')")
    page.wait_for_timeout(100)
    page.fill("#document_max_pages", "abc")
    page.click("#btn-apply")
    page.wait_for_selector("#global-error-text .banner-jump-btn", timeout=5000)

    page.evaluate("switchTab('general')")
    page.wait_for_timeout(100)
    page.click("#global-error-text .banner-jump-btn")
    page.wait_for_timeout(100)

    samples = page.evaluate(SAMPLE_PULSE, "document_max_pages")
    assert_pulse_visible(samples)

    still_dirty = page.evaluate(
        "document.getElementById('document_max_pages').classList.contains('dirty-field')"
    )
    assert still_dirty, "the dirty marker itself must survive the pulse"

    nav = read_navigation(page, "document_max_pages")
    assert nav["active_tab"] == "docs"
    assert nav["field_in_viewport"]
    assert nav["value"] == "abc", "navigation must never mutate the field"


def test_repeat_click_restarts_the_pulse(open_page):
    page = open_page({"DOCUMENT_RANGE": "abc"})
    page.click("#global-error-text .banner-jump-btn")
    page.wait_for_timeout(1900)

    page.click("#global-error-text .banner-jump-btn")
    page.wait_for_timeout(100)
    samples = page.evaluate(SAMPLE_PULSE, "document_range")
    assert_pulse_visible(samples)



def plant_old_database(tmp_path):
    import sqlite3
    tech = tmp_path / "output" / "current_run" / "TECH"
    tech.mkdir(parents=True)
    connection = sqlite3.connect(tech / "application_state.db")
    try:
        connection.execute(
            "CREATE TABLE llm_requests (legacy_id INTEGER NOT NULL, file_id INTEGER NOT NULL,"
            " PRIMARY KEY (legacy_id))")
        connection.execute("INSERT INTO llm_requests VALUES (1, 1)")
        connection.commit()
    finally:
        connection.close()


def test_a_refusal_dialog_links_to_the_setting_it_names(open_page, tmp_path):
    plant_old_database(tmp_path)
    page = open_page({"START_OVER": False, "ENABLE_LLM_INFERENCE": False})
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    page.evaluate("switchTab('docs')")
    page.wait_for_timeout(100)

    page.click("#btn-start")
    page.wait_for_selector("#modal-jump-link", timeout=5000)
    assert page.evaluate("document.getElementById('modal-jump-link').textContent") == \
        "Start a fresh new run"
    message = page.evaluate("document.getElementById('modal-message').textContent")
    assert "Start a fresh new run" in message
    assert "application_state.db" not in message, "the detail belongs in the path box, not the sentence"
    detail = page.evaluate("document.getElementById('modal-path').textContent")
    assert detail == "", "no action on the database file is part of this recovery"
    assert page.evaluate(
        "getComputedStyle(document.getElementById('modal-path')).display") == "none"

    page.click("#modal-jump-link")
    page.wait_for_timeout(100)
    assert page.evaluate(
        "getComputedStyle(document.getElementById('modal-overlay')).display") == "none"
    samples = page.evaluate(SAMPLE_PULSE, "START_OVER")
    assert_pulse_visible(samples)
    nav = read_navigation(page, "START_OVER")
    assert nav["active_tab"] == "general"
    assert nav["field_in_viewport"]
    assert page.evaluate("document.getElementById('START_OVER').checked") is False, \
        "navigation must never mutate the setting"
    assert page.evaluate("document.getElementById('btn-start').disabled") is False


def test_the_refusal_link_works_from_the_keyboard(open_page, tmp_path):
    plant_old_database(tmp_path)
    page = open_page({"START_OVER": False, "ENABLE_LLM_INFERENCE": False})
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    page.evaluate("switchTab('docs')")
    page.wait_for_timeout(100)
    page.click("#btn-start")
    page.wait_for_selector("#modal-jump-link", timeout=5000)
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.activeElement.id") == "modal-jump-link"
    page.keyboard.press("Enter")
    page.wait_for_timeout(100)
    assert page.evaluate(
        "getComputedStyle(document.getElementById('modal-overlay')).display") == "none"
    samples = page.evaluate(SAMPLE_PULSE, "START_OVER")
    assert_pulse_visible(samples)
    nav = read_navigation(page, "START_OVER")
    assert nav["active_tab"] == "general" and nav["field_in_viewport"]


def test_a_refused_start_keeps_a_banner_until_the_cause_is_addressed(open_page, tmp_path):
    plant_old_database(tmp_path)
    page = open_page({"START_OVER": False, "ENABLE_LLM_INFERENCE": False})
    page.set_viewport_size({"width": APP_WIDTH, "height": APP_HEIGHT})
    banner_state = """() => {
        const banner = document.querySelector('[data-notice-id="start"]');
        const link = document.getElementById('refusal-jump-link');
        return {
            shown: !!banner && getComputedStyle(banner).display !== 'none',
            text: banner ? banner.textContent : '',
            link: link ? link.textContent : null,
            start_disabled: document.getElementById('btn-start').disabled,
        };
    }"""
    assert not page.evaluate(banner_state)["shown"]

    page.click("#btn-start")
    page.wait_for_selector("#modal-jump-link", timeout=5000)
    page.click("#modal-ok")
    page.wait_for_timeout(150)
    state = page.evaluate(banner_state)
    assert state["shown"] and "Start a fresh new run" in state["text"]
    assert state["link"] == "Start a fresh new run"
    assert state["start_disabled"] is False, "the notice must not lock Start"

    page.evaluate("switchTab('videos')")
    page.wait_for_timeout(100)
    assert page.evaluate(banner_state)["shown"]
    page.click("#refusal-jump-link")
    page.wait_for_timeout(100)
    samples = page.evaluate(SAMPLE_PULSE, "START_OVER")
    assert_pulse_visible(samples)
    assert read_navigation(page, "START_OVER")["active_tab"] == "general"
    assert page.evaluate(banner_state)["shown"]
    rect = page.evaluate("""() => {
        const r = document.querySelector('[data-notice-id="start"]').getBoundingClientRect();
        const s = document.getElementById('main-scroll').getBoundingClientRect();
        return {top: r.top, bottom: r.bottom, scrollerTop: s.top, scrollerBottom: s.bottom};
    }""")
    assert rect["top"] >= 0 and rect["bottom"] <= rect["scrollerTop"], \
        f"the notice must stay in view after the jump: {rect}"
    assert read_navigation(page, "START_OVER")["field_in_viewport"]

    page.check("#START_OVER")
    page.click("#btn-apply")
    page.wait_for_timeout(600)
    assert not page.evaluate(banner_state)["shown"], "a successful Apply clears the notice"


def test_a_start_that_goes_through_clears_the_refusal_banner(open_page, tmp_path):
    plant_old_database(tmp_path)
    page = open_page({"START_OVER": False, "ENABLE_LLM_INFERENCE": False})
    page.click("#btn-start")
    page.wait_for_selector("#modal-jump-link", timeout=5000)
    page.click("#modal-ok")
    page.wait_for_timeout(150)
    shown = "!!document.querySelector('[data-notice-id=start]')"
    assert page.evaluate(shown)

    (tmp_path / "output" / "current_run" / "TECH" / "application_state.db").unlink()
    page.click("#btn-start")
    page.wait_for_timeout(1500)
    assert not page.evaluate(shown), "a Start that goes through clears the notice"


def test_the_refusal_link_and_message_speak_the_current_locale(open_page, tmp_path):
    plant_old_database(tmp_path)
    page = open_page({"START_OVER": False, "ENABLE_LLM_INFERENCE": False})
    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(200)
    page.click("#btn-start")
    page.wait_for_selector("#modal-jump-link", timeout=5000)
    label = page.evaluate("document.getElementById('modal-jump-link').textContent")
    message = page.evaluate("document.getElementById('modal-message').textContent")
    assert label == "Начать новую сессию"
    assert label in message



def test_unrelated_apply_preserves_refusal_and_locale(open_page, tmp_path):
    plant_old_database(tmp_path)
    page = open_page({"START_OVER": False, "ENABLE_LLM_INFERENCE": False})
    page.set_viewport_size({"width": 1280, "height": 800})
    page.click("#btn-start")
    page.wait_for_selector("#modal-jump-link")
    page.click("#modal-ok")
    page.evaluate("switchTab('output')")
    page.locator('[name="JPEG_QUALITY"]').fill('89')
    with page.expect_response('**/api/settings/commit'):
        page.click('#btn-apply')
    page.wait_for_function('!window.hasUnsavedEdits()')
    assert page.locator('[data-notice-id="start"]').is_visible()
    page.evaluate("changeLanguage('ru')")
    assert page.locator('[data-notice-id="start"]').is_visible()
    assert page.locator('#refusal-jump-link').inner_text() == 'Начать новую сессию'
    assert page.locator('#START_OVER').is_checked() is False


def test_named_references_resolve_prompt_mode_and_are_text_only(open_page):
    page = open_page({'LLM_SYSTEM_PROMPT_MODE': 'FILE'})
    result = page.evaluate("""() => {
        const ref = window.settingRef('LLM_SYSTEM_PROMPT');
        const node = document.createElement('div');
        window.renderMessage(node, {text: '{one} / {two} / {raw}',
            refs: {one: 'MAX_DIMENSION', two: 'LOWEST_QUALITY'},
            args: {raw: '<img src=x onerror=alert(1)>'}});
        return {field: ref.element.id, label: ref.label, links: node.querySelectorAll('a').length,
            image: !!node.querySelector('img'), text: node.textContent};
    }""")
    assert result['field'] == 'sys_file_input'
    assert result['label'] == 'System Prompt'
    assert result['links'] == 2 and not result['image']
    assert '<img' in result['text']



def test_old_database_dialog_gives_recovery_steps_without_a_database_path(open_page, tmp_path):
    from pathlib import Path
    plant_old_database(tmp_path)
    page = open_page({'START_OVER': False, 'ENABLE_LLM_INFERENCE': False})
    for locale in sorted((Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json')):
        page.evaluate('changeLanguage', locale.stem)
        page.click('#btn-start')
        page.wait_for_selector('#modal-jump-link')
        dialog = page.locator('#modal-dialog')
        text = dialog.inner_text()
        assert all(marker in text for marker in ('1.', '2.', '3.'))
        assert 'application_state.db' not in text
        assert page.locator('#modal-path').inner_text() == ''
        assert not page.locator('#modal-path-copy').is_visible()
        assert not page.locator('#START_OVER').is_checked()
        page.click('#modal-ok')



def test_notice_owners_survive_replacement_translation_and_unrelated_clear(open_page):
    from pathlib import Path

    page = open_page({})
    page.evaluate("""() => {
        notice({id: 'first', text: 'Old operation', summaryKey: 'notice_action_failed'});
        notice({id: 'second', text: 'Independent failure', summaryKey: 'notice_action_failed'});
        notice({id: 'first', text: 'Replacement failure', summaryKey: 'notice_action_failed'});
    }""")
    for locale in sorted((Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json')):
        page.evaluate('changeLanguage', locale.stem)
        page.evaluate('renderErrors()')
        assert page.locator('[data-notice-id="first"]').count() == 1
        assert page.locator('[data-notice-id="second"]').count() == 1
        page.locator('[data-notice-id="first"] button').click()
        assert page.locator('#modal-message').inner_text() == 'Replacement failure'
        page.keyboard.press('Escape')
    page.evaluate("clearNotice('first')")
    assert page.locator('[data-notice-id="first"]').count() == 0
    assert page.locator('[data-notice-id="second"]').is_visible()
    page.locator('[data-notice-id="second"] button').click()
    assert page.locator('#modal-message').inner_text() == 'Independent failure'


def test_refusal_failed_apply_keeps_original_notice_and_disk_bytes(open_page, tmp_path):
    plant_old_database(tmp_path)
    page = open_page({'START_OVER': False})
    before = (tmp_path / 'settings.json').read_bytes()
    page.click('#btn-start')
    page.wait_for_selector('#modal-jump-link')
    page.click('#modal-ok')
    page.locator('#START_OVER').check()
    page.evaluate("switchTab('output')")
    page.locator('[name="JPEG_QUALITY"]').fill('0')
    with page.expect_response('**/api/settings/commit') as response:
        page.click('#btn-apply')
    assert response.value.json()['status'] == 'error'
    page.wait_for_function('window.applyErrors !== null')
    assert page.locator('[data-notice-id="start"]').is_visible()
    assert (tmp_path / 'settings.json').read_bytes() == before


def test_live_setting_label_is_rendered_once_and_hostile_arguments_stay_literal(open_page):
    from pathlib import Path

    page = open_page({})
    raw = '<img src=x onerror="window.injected=1"> {quality} & $&'
    for locale in sorted((Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json')):
        page.evaluate('changeLanguage', locale.stem)
        result = page.evaluate(r"""raw => {
            const label = document.getElementById("MAX_DIMENSION").closest(".form-group").querySelector("label");
            label.textContent = 'Fresh label <b> & ' + raw;
            const node = document.createElement('div');
            renderMessage(node, {text: '{resolution}\n{quality}\n{raw}',
                refs: {resolution: 'MAX_DIMENSION', quality: 'LOWEST_QUALITY'}, args: {raw}});
            return {links: [...node.querySelectorAll('a')].map(a => [a.dataset.setting, a.textContent]),
                markup: node.querySelectorAll('img,b,script').length, text: node.textContent};
        }""", raw)
        assert result['links'][0] == ['MAX_DIMENSION', 'Fresh label <b> & ' + raw]
        assert [link[0] for link in result['links']] == ['MAX_DIMENSION', 'LOWEST_QUALITY']
        assert result['markup'] == 0
        assert result['text'].endswith(raw)


def test_notice_provider_link_reaches_the_named_control_after_provider_changes(open_page, tmp_path):
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'openai'},
                     tokens={'openai': 'sk-fake0123456789abcdef0123456789abcdef'})
    page.evaluate("switchTab('ai')")
    page.evaluate("""() => {
        notice({id: 'start', key: 'err_settings_invalid',
            field: 'LLM_PROVIDERS.openai.max_tokens', summaryKey: 'notice_start_setting'});
    }""")
    page.locator('[name="LLM_PROVIDER"]').select_option('ollama')
    before = page.evaluate('JSON.stringify(window.draftState)')
    disk = (tmp_path / 'settings.json').read_bytes()
    page.locator('[data-notice-id="start"] a').click()
    page.wait_for_timeout(400)
    target = page.locator('[name="LLM_PROVIDERS.openai.max_tokens"]')
    assert target.is_visible(), 'A live setting link must not silently target a hidden provider frame'
    assert target.evaluate('(el) => document.activeElement === el')
    assert page.evaluate('JSON.stringify(window.draftState)') == before
    assert (tmp_path / 'settings.json').read_bytes() == disk


def test_unknown_message_key_uses_readable_fallback_on_real_notice_surface(open_page):
    from pathlib import Path

    page = open_page({})
    for locale in sorted((Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json')):
        page.evaluate('changeLanguage', locale.stem)
        page.evaluate("""() => { notice({id: 'missing-key', key: 'err_nonexistent_review_probe'}); }""")
        expected = page.evaluate("getT('msg_translation_missing')")
        assert page.locator('[data-notice-id="missing-key"] span').first.inner_text() == expected



def test_prompt_setting_links_preserve_both_representations_and_drafts(open_page, tmp_path):
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'ollama'})
    page.evaluate("switchTab('ai')")
    for kind, field, mode_field in [('sys', 'LLM_SYSTEM_PROMPT', 'LLM_SYSTEM_PROMPT_MODE'),
                                     ('user', 'LLM_USER_PROMPT', 'LLM_USER_PROMPT_MODE')]:
        for mode in ('TEXT', 'FILE'):
            page.locator(f'[name="{mode_field}"]').select_option(mode)
            selector = f'#{kind}_{"text" if mode == "TEXT" else "file"}_input'
            page.locator(selector).fill('unsaved literal draft <tag> ' + mode)
            draft = page.evaluate('JSON.stringify(window.draftState)')
            disk = (tmp_path / 'settings.json').read_bytes()
            page.evaluate("""field => {
                switchTab('general');
                notice({id: 'prompt-reference', key: 'err_settings_invalid', field,
                    summaryKey: 'notice_start_setting'});
            }""", field)
            page.locator('[data-notice-id="prompt-reference"] a').click()
            page.wait_for_timeout(400)
            assert page.locator(selector).is_visible()
            assert page.locator(selector).evaluate('(el) => el === document.activeElement')
            assert page.evaluate('JSON.stringify(window.draftState)') == draft
            assert (tmp_path / 'settings.json').read_bytes() == disk


def test_missing_setting_target_has_readable_fallback(open_page):
    page = open_page({})
    page.evaluate("notice({id:'probe', key:'notice_start_setting', field:'MISSING_FIELD'})")
    text = page.locator('[data-notice-id="probe"]').inner_text()
    assert '{setting}' not in text, text


@pytest.mark.parametrize('ai_enabled', [True, False])
def test_provider_inspection_preserves_drafts_and_blocks_edits(open_page, tmp_path, ai_enabled):
    from pathlib import Path

    page = open_page({'ENABLE_LLM_INFERENCE': ai_enabled, 'LLM_PROVIDER': 'ollama'})
    page.set_viewport_size({'width': 1280, 'height': 800})
    page.evaluate("switchTab('ai')")
    if ai_enabled:
        page.locator('[name="LLM_PROVIDERS.ollama.model"]').fill('unsaved-model')
    disk = (tmp_path / 'settings.json').read_bytes()
    draft = page.evaluate('JSON.stringify(draftState)')
    dirty = page.evaluate('hasUnsavedEdits()')
    for locale in sorted((Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json')):
        page.evaluate('changeLanguage', locale.stem)
        page.evaluate("goToField('LLM_PROVIDERS.openai.max_tokens')")
        target = page.locator('[name="LLM_PROVIDERS.openai.max_tokens"]')
        page.wait_for_function("document.activeElement.name === 'LLM_PROVIDERS.openai.max_tokens'")
        assert target.is_visible()
        assert target.evaluate('el => el.readOnly')
        assert page.locator('#provider-ollama').is_visible()
        assert page.locator('#LLM_PROVIDER').input_value() == 'ollama'
        before = target.input_value()
        page.keyboard.type('123456')
        assert target.input_value() == before
        toggle = page.locator('#req_max_openai')
        checked = toggle.is_checked()
        toggle.focus()
        page.keyboard.press('Space')
        assert toggle.is_checked() == checked
        assert page.locator('#provider-openai .btn-default').first.is_disabled()
        assert page.evaluate('JSON.stringify(draftState)') == draft
        assert page.evaluate('hasUnsavedEdits()') == dirty
        note = page.locator('[data-notice-id="provider-inspection"]')
        assert note.is_visible()
        assert note.evaluate('(el) => el.scrollWidth <= el.clientWidth')
        page.screenshot(path=str(tmp_path / f'provider-inspection-{locale.stem}-{ai_enabled}.png'))
        page.locator('[data-notice-id="provider-inspection"] button').click()
        assert not target.is_visible()
    assert (tmp_path / 'settings.json').read_bytes() == disk


def test_unknown_keys_and_targets_fall_back_on_every_message_surface(open_page):
    page = open_page({})
    for language in ('en', 'ru'):
        page.evaluate('changeLanguage', language)
        result = page.evaluate("""() => {
            const host = document.createElement('div');
            renderMessage(host, {key:'err_missing_probe', text:'Useful raw explanation'});
            const raw = host.textContent;
            renderMessage(host, {key:'err_missing_probe'});
            const missing = host.textContent;
            return {raw, missing, expected: getT('msg_translation_missing')};
        }""")
        assert result['raw'] == 'Useful raw explanation'
        assert result['missing'] == result['expected']
        page.evaluate("() => { appAlert({key:'err_missing_probe'}); }")
        assert page.locator('#modal-message').inner_text() == result['expected']
        page.click('#modal-ok')



from pathlib import Path
import json

RECOVERY_LOCALES = sorted(path.stem for path in (Path(__file__).resolve().parents[2] / "src/locales").glob("*.json"))
RECOVERY_DETAILS = {
    "context": ("err_resume_ai_context_changed_detail",
                "The source folder or effective processing settings changed.",
                "Изменилась папка источников или действующие настройки обработки."),
}


def _expected_recovery_detail(locale, cause):
    key, english, russian = RECOVERY_DETAILS[cause]
    if locale == "en":
        return english
    if locale == "ru":
        return russian
    strings = json.loads((Path(__file__).resolve().parents[2] / "src/locales" / f"{locale}.json").read_text("utf-8"))
    return strings[key]


@pytest.mark.parametrize("locale", RECOVERY_LOCALES)
@pytest.mark.parametrize("cause", ["root", "request"])
def test_ai_recovery_refusal_names_unfinished_work_and_keeps_the_restart_link(
        open_page, tmp_path, monkeypatch, locale, cause):
    from routes import execution_api
    from db_controller import SQLiteDatabaseController

    db = SQLiteDatabaseController(tmp_path / "saved.db")
    db.record_run_configuration(True, "table_per_file", ("answer",))
    db.ensure_run_identity("original-root", "original-settings")

    def refused_core(*args, **kwargs):
        db.ensure_run_identity("other-root" if cause == "root" else "original-root",
                               "other-settings" if cause == "request" else "original-settings")
        pytest.fail("Changed processing inputs were accepted")

    monkeypatch.setattr(execution_api, "ProcessorCore", refused_core)
    try:
        page = open_page({"START_OVER": False, "ENABLE_LLM_INFERENCE": False})
        page.set_viewport_size({"width": 1280, "height": 800})
        page.evaluate("locale => changeLanguage(locale)", locale)
        with page.expect_response("**/api/process/start") as response:
            page.click("#btn-start")
        payload = response.value.json()
        assert payload["message_key"] == "err_resume_ai_work_changed"
        assert payload["field"] == "START_OVER"
        assert payload["path"] == ""
        page.wait_for_selector("#modal-jump-link")
        message = page.locator("#modal-message").inner_text()
        if locale in ("en", "ru"):
            expected = "This run cannot continue" if locale == "en" else "Продолжить запуск нельзя"
            assert expected in message
        assert "report shapes" not in message and "форматы отчёта" not in message
        assert page.locator("#modal-jump-link").inner_text() in message
        assert "{" not in message and "undefined" not in message
        assert not page.locator("#modal-path").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        detail_cause = "context"
        expected_detail = _expected_recovery_detail(locale, detail_cause)
        assert page.locator("#modal-detail").inner_text() == expected_detail
        page.evaluate("""() => {
            window.copiedRecoveryDetail = null;
            window.copyTextToClipboard = text => { window.copiedRecoveryDetail = text; };
        }""")
        page.locator("#modal-detail-copy").click()
        assert page.evaluate("window.copiedRecoveryDetail") == expected_detail
        page.locator("#modal-detail-copy").focus()
        for next_locale in RECOVERY_LOCALES:
            page.evaluate("locale => changeLanguage(locale)", next_locale)
            assert page.locator("#modal-detail").inner_text() == _expected_recovery_detail(next_locale, detail_cause)
            assert page.evaluate("document.activeElement.id") == "modal-detail-copy"
        page.evaluate("locale => changeLanguage(locale)", locale)
        screenshot = tmp_path / f"ai_recovery_{cause}_{locale}.png"
        page.screenshot(path=str(screenshot))
        print(f"AI_RECOVERY_SCREENSHOT {screenshot}")
        page.click("#modal-ok")
        page.locator('[data-notice-id="start"] [data-notice-action="btn_details"]').click()
        assert page.locator("#modal-detail").inner_text() == expected_detail
        page.click("#modal-jump-link")
        page.wait_for_timeout(100)
        assert read_navigation(page, "START_OVER")["active_tab"] == "general"
        assert page.locator("#START_OVER").is_checked() is False
    finally:
        db.close()


@pytest.mark.parametrize("locale", RECOVERY_LOCALES)
@pytest.mark.parametrize("raw", [
    "err_resume_ai_work_changed", "<img src=x onerror=window.detailInjected=true>|{literal}",
])
def test_literal_diagnostics_are_not_interpreted_as_translations_or_markup(open_page, locale, raw):
    page = open_page({})
    page.evaluate("locale => changeLanguage(locale)", locale)
    page.evaluate("raw => { appAlert({key:'notice_action_failed', detail:raw}); }", raw)
    assert page.locator("#modal-detail").inner_text() == raw
    assert page.locator("#modal-detail img").count() == 0
    assert page.evaluate("window.detailInjected === undefined")
    for next_locale in RECOVERY_LOCALES:
        page.evaluate("locale => changeLanguage(locale)", next_locale)
        assert page.locator("#modal-detail").inner_text() == raw


@pytest.mark.parametrize("locale", RECOVERY_LOCALES)
@pytest.mark.parametrize("fallback", ["Useful literal diagnostic", "", "err_missing_detail_probe"])
def test_detail_translation_has_readable_missing_key_fallback(open_page, locale, fallback):
    page = open_page({})
    page.evaluate("locale => changeLanguage(locale)", locale)
    page.evaluate("""fallback => {
        appAlert({key:'notice_action_failed', detailKey:'err_missing_detail_probe', detail:fallback});
    }""", fallback)
    strings = json.loads((Path(__file__).resolve().parents[2] / "src/locales" / f"{locale}.json").read_text("utf-8"))
    expected = (fallback if fallback and fallback != "err_missing_detail_probe"
                else strings["msg_translation_missing"])
    assert page.locator("#modal-detail").is_visible()
    assert page.locator("#modal-detail").inner_text() == expected



@pytest.mark.parametrize("locale", RECOVERY_LOCALES)
@pytest.mark.parametrize("cause,hold_ack", [("root", False), ("request", False), ("worker", False), ("worker", True)])
def test_real_recovery_refusals_reach_translated_details_before_and_after_start(
        open_page, tmp_path, monkeypatch, locale, cause, hold_ack):
    import threading
    from PIL import Image
    from app_context import ProcessorCore
    from config_loader import load_strict
    from schemas import FileSummary, PageResult
    from fake_llm.generic import GenericServer

    server = GenericServer().start()
    try:
        page = open_page({"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "custom",
                          "LLM_PROVIDERS": {"custom": {"url": server.url, "model": "fixture-model"}},
                          "START_OVER": False, "MAX_JPEGS_PER_INFERENCE": 1})
        settings = load_strict()
        Image.new("RGB", (8, 8), "red").save(settings.INPUT_FOLDER_PATH / "source.png")
        core = ProcessorCore(settings, threading.Event())
        try:
            assert core.llm is not None
            db = core.database_controller
            root = settings.CURRENT_RUN_FOLDER
            image_path = root / "saved.jpg"
            Image.new("RGB", (8, 8), "red").save(image_path)
            db.handle_file_started(1, "source.png", ".png", "Generated", ai_enabled=True)
            db.handle_frame_saved(1, PageResult(1, image_path.name, "ok", ""))
            db.handle_file_completed(1, FileSummary(1, "1", "ok", "done"))
        finally:
            core.shutdown()
        page.evaluate("locale => changeLanguage(locale)", locale)
        if cause == "worker":
            from batch_orchestrator import BatchOrchestrator
            from schemas import ConfigurationError
            def refused_work(*args, **kwargs):
                raise ConfigurationError(
                    "i18n:err_resume_ai_work_changed|The source folder or effective processing settings changed.",
                    setting_field="START_OVER", detail_key="err_resume_ai_context_changed_detail")
            monkeypatch.setattr(BatchOrchestrator, "execute_batch_processing_loop", refused_work)
        else:
            if cause == "root":
                other = tmp_path / "other_input"
                other.mkdir()
                page.locator('[name="INPUT_FOLDER_PATH"]').fill(str(other))
            else:
                page.evaluate("switchTab('ai')")
                page.locator('[name="MAX_JPEGS_PER_INFERENCE"]').fill("2")
            with page.expect_response("**/api/settings/commit"):
                page.click("#btn-apply")
            page.wait_for_function("!window.settingsSavePending && !window.hasUnsavedEdits()")
        page.evaluate("""() => {
            const finish = window.finishAutomaticExportRun;
            window.finishAutomaticExportRun = (...args) => {
                finish(...args);
                queueMicrotask(() => { window.startAckApplied = true; });
            };
        }""")
        if hold_ack:
            page.evaluate("""() => {
                const original = window.fetch;
                window.fetch = async (url, options) => {
                    const response = await original(url, options);
                    if (url !== '/api/process/start') return response;
                    const body = await response.text();
                    window.heldStartStatus = response.status;
                    return new Promise(resolve => {
                        window.releaseStartAck = () => resolve(new Response(body, {status: response.status,
                            headers: {'Content-Type':'application/json'}}));
                    });
                };
            }""")
        if cause == "worker":
            page.click("#btn-start")
            page.wait_for_function("document.getElementById('run-status').classList.contains('status-failed')")
            if hold_ack:
                page.wait_for_function("typeof window.releaseStartAck === 'function'")
                assert page.evaluate("window.heldStartStatus") == 200
                page.evaluate("window.releaseStartAck()")
            page.wait_for_function("window.startAckApplied === true")
            notice = page.locator('[data-notice-id="run"]')
            assert notice.is_visible()
            notice.locator('[data-notice-action="btn_details"]').click()
        else:
            with page.expect_response("**/api/process/start") as response:
                page.click("#btn-start")
            assert response.value.status == 400
            page.wait_for_selector("#modal-jump-link")
        assert page.evaluate("window.runActive") is False
        assert page.locator("#btn-stop").is_disabled()
        detail_cause = "context"
        assert page.locator("#modal-detail").inner_text() == _expected_recovery_detail(locale, detail_cause)
        assert not server.requests, "incompatible recovery work must not be uploaded"
        for next_locale in RECOVERY_LOCALES:
            page.evaluate("locale => changeLanguage(locale)", next_locale)
            assert page.locator("#modal-detail").inner_text() == _expected_recovery_detail(next_locale, detail_cause)
    finally:
        server.stop()


def test_detail_localization_oracle_rejects_lost_translation_metadata(open_page):
    page = open_page({})
    page.evaluate("changeLanguage('ru')")
    key, english, russian = RECOVERY_DETAILS["context"]
    payload = {"message_key": "err_resume_ai_work_changed", "detail": english,
               "detail_key": key, "field": "START_OVER"}

    def show_and_check(data):
        page.evaluate("data => { appAlert(serverNotice(data)); }", data)
        assert page.locator("#modal-detail").inner_text() == russian

    show_and_check(payload)
    broken = {k: v for k, v in payload.items() if k != "detail_key"}
    with pytest.raises(AssertionError):
        show_and_check(broken)
    show_and_check(payload)



@pytest.mark.parametrize("locale", RECOVERY_LOCALES)
def test_settings_validation_details_keep_translation_intent_and_live_setting_references(open_page, tmp_path, locale):
    page = open_page({"JPEG_QUALITY": 90, "LOWEST_QUALITY": 25})
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate("locale => changeLanguage(locale)", locale)
    path = tmp_path / "settings.json"
    assert path.is_file()
    values = json.loads(path.read_text(encoding="utf-8"))
    values["LOWEST_QUALITY"] = 91
    path.write_text(json.dumps(values), encoding="utf-8")
    with page.expect_response("**/api/process/start") as response:
        page.click("#btn-start")
    assert response.value.status == 400
    page.wait_for_selector("#modal-jump-link")
    detail = page.locator("#modal-detail").inner_text()
    assert "err_quality_floor_above_start" not in detail
    assert "{floor}" not in detail and "{start}" not in detail
    references = page.locator("#modal-detail [data-setting]").evaluate_all(
        "links => Array.from(new Set(links.map(link => link.dataset.setting))).sort()")
    assert references == ["JPEG_QUALITY", "LOWEST_QUALITY"]
    assert page.locator("#modal-jump a").count() == 0, "detail references already provide the setting action"
    for key in references:
        expected = page.locator(f'[name="{key}"]').evaluate(
            "el => el.closest('.form-group').querySelector('label').textContent").strip().rstrip(":")
        assert page.locator(f'#modal-detail [data-setting="{key}"]').first.inner_text() == expected
    if locale == "ru":
        assert "не может быть выше" in detail
    screenshot = tmp_path / f"validation_detail_{locale}.png"
    page.screenshot(path=str(screenshot))
    print(f"VALIDATION_DETAIL_SCREENSHOT {screenshot}")
    before = page.locator('[name="LOWEST_QUALITY"]').input_value()
    page.locator('#modal-detail [data-setting="LOWEST_QUALITY"]').first.click()
    assert not page.locator("#modal-overlay").is_visible()
    assert page.locator('[name="LOWEST_QUALITY"]').input_value() == before
