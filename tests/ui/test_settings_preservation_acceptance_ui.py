# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from fake_llm.harness import READY_ENTRY, ready_ai


def saved(tmp_path):
    return json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))


def apply(page):
    with page.expect_response("**/api/settings/commit") as response:
        page.click("#btn-apply")
    assert response.value.status == 200, response.value.json()
    page.wait_for_function("!window.settingsSavePending && document.getElementById('btn-apply').disabled")
    return response.value.json()


@pytest.mark.parametrize("field,old,new,selector,tab", [
    ("MAX_DIMENSION", 1920, 1234, '[name="MAX_DIMENSION"]', "output"),
    ("LLM_USER_PROMPT", "original", "external prompt", '#user_text_input', "ai"),
])
def test_apply_reconciles_untouched_controls_with_fresh_disk_values(
        open_page, tmp_path, field, old, new, selector, tab):
    page = open_page({**ready_ai(), field: old})
    raw = saved(tmp_path)
    raw[field] = new
    raw["external.future"] = {"empty": {}, "large": 9007199254740993}
    (tmp_path / "settings.json").write_text(json.dumps(raw), encoding="utf-8")
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', "81")
    apply(page)
    assert saved(tmp_path)[field] == new
    assert saved(tmp_path)["external.future"] == raw["external.future"]
    assert str(page.evaluate("key => window.draftState[key]", field)) == str(new)
    assert not page.evaluate("window.hasUnsavedEdits()")
    page.evaluate("tab => window.switchTab(tab)", tab)
    assert page.locator(selector).input_value() == str(new), "clean controls must show the actual saved value"
    assert page.locator("#btn-start").is_enabled()


@pytest.mark.parametrize("field,selector,bad,text", [
    ("LLM_USER_PROMPT", "#user_text_input", {"a": 1}, '{"a": 1}'),
    ("LLM_SYSTEM_PROMPT", "#sys_text_input", ["answer"], '["answer"]'),
    ("LLM_OUTPUT_COLUMNS", '[name="LLM_OUTPUT_COLUMNS"]', 123, "123"),
    ("LLM_PROVIDERS.custom.model", '[name="LLM_PROVIDERS.custom.model"]', 123, "123"),
    ("LLM_PROVIDERS.custom.extra_header_value", '[name="LLM_PROVIDERS.custom.extra_header_value"]', None, ""),
])
@pytest.mark.parametrize("distinct_text", [False, True], ids=["same-display", "different-display"])
def test_explicit_text_repair_is_not_confused_with_invalid_stored_type(
        open_page, tmp_path, field, selector, bad, text, distinct_text):
    if distinct_text:
        text = "repaired"
    raw = ready_ai()
    if field.startswith("LLM_PROVIDERS."):
        raw["LLM_PROVIDERS"]["custom"][field.split(".")[-1]] = bad
    else:
        raw[field] = bad
    page = open_page(raw)
    page.evaluate("window.switchTab('ai')")
    assert page.evaluate("key => key in window.diskErrors", field)
    control = page.locator(selector)
    control.fill("temporary explicit edit")
    control.fill(text)
    control.dispatch_event("change")
    assert page.evaluate("window.hasUnsavedEdits()"), (
        "a valid string repair must differ from the invalid stored JSON type")
    apply(page)
    result = saved(tmp_path)
    actual = (result["LLM_PROVIDERS"]["custom"][field.split(".")[-1]]
              if field.startswith("LLM_PROVIDERS.") else result[field])
    assert type(actual) is str and actual == text
    assert page.locator("#btn-start").is_enabled()


@pytest.mark.parametrize("field,selector", [
    ("LLM_MAX_RETRIES", '[name="LLM_MAX_RETRIES"]'),
    ("LLM_PROVIDERS.custom.max_tokens", '[name="LLM_PROVIDERS.custom.max_tokens"]'),
])
@pytest.mark.parametrize("original,edited", [(9007199254740992, "9007199254740993"),
                                            (9007199254740993, "09007199254740993"),
                                            (9007199254740993, "9007199254740992")])
def test_exact_integer_edit_and_equivalent_spelling_controls(open_page, tmp_path, field, selector, original, edited):
    raw = ready_ai()
    if field.startswith("LLM_PROVIDERS."):
        raw["LLM_PROVIDERS"]["custom"]["max_tokens"] = original
    else:
        raw[field] = original
    page = open_page(raw)
    page.evaluate("window.switchTab('ai')")
    page.fill(selector, edited)
    assert page.evaluate("window.hasUnsavedEdits()") is (int(edited) != original)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', "81")
    apply(page)
    result = saved(tmp_path)
    value = result["LLM_PROVIDERS"]["custom"]["max_tokens"] if field.startswith("LLM_PROVIDERS.") else result[field]
    assert type(value) is int and value == int(edited)


@pytest.mark.parametrize("bad", [None, "garbage", {}, []])
def test_invalid_checkbox_can_be_explicitly_repaired_to_false(open_page, tmp_path, bad):
    page = open_page(ready_ai(require_max_tokens=bad))
    page.evaluate("window.switchTab('ai')")
    box = page.locator("#req_max_custom")
    box.check()
    box.uncheck()
    assert page.evaluate("window.hasUnsavedEdits()")
    apply(page)
    assert saved(tmp_path)["LLM_PROVIDERS"]["custom"]["require_max_tokens"] is False


@pytest.mark.parametrize("field,selector", [
    ("LLM_OUTPUT_COLUMNS", '[name="LLM_OUTPUT_COLUMNS"]'),
    ("LLM_PROVIDERS.custom.model", '[name="LLM_PROVIDERS.custom.model"]'),
])
def test_explicit_string_repair_is_included_with_an_unrelated_edit(open_page, tmp_path, field, selector):
    raw = ready_ai()
    if field.startswith("LLM_PROVIDERS."):
        raw["LLM_PROVIDERS"]["custom"]["model"] = 123
    else:
        raw[field] = 123
    page = open_page(raw)
    page.evaluate("window.switchTab('ai')")
    page.fill(selector, "temporary")
    page.fill(selector, "123")
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', "81")
    apply(page)
    result = saved(tmp_path)
    value = result["LLM_PROVIDERS"]["custom"]["model"] if field.startswith("LLM_PROVIDERS.") else result[field]
    assert type(value) is str and value == "123"


@pytest.mark.parametrize("field,selector,text,kind", [
    ("LLM_MAX_RETRIES", '[name="LLM_MAX_RETRIES"]', "0007", "text"),
    ("LLM_USER_PROMPT", '#user_text_input', 'new prompt <literal>', "text"),
    ("HALT_ON_LLM_PARSE_ERROR", '[name="HALT_ON_LLM_PARSE_ERROR"][type="checkbox"]', False, "checkbox"),
    ("LLM_PROVIDERS.custom.max_tokens", '[name="LLM_PROVIDERS.custom.max_tokens"]', "00127", "text"),
])
def test_explicit_edit_before_disabling_ai_retains_its_submitted_value(
        open_page, tmp_path, field, selector, text, kind):
    page = open_page(ready_ai())
    page.evaluate("window.switchTab('ai')")
    if kind == "checkbox":
        page.locator(selector).uncheck()
    else:
        page.fill(selector, text)
    page.evaluate("window.switchTab('general')")
    page.select_option('#ENABLE_LLM_INFERENCE', 'false')
    apply(page)
    result = saved(tmp_path)
    value = result["LLM_PROVIDERS"]["custom"]["max_tokens"] if field.startswith("LLM_PROVIDERS.") else result[field]
    assert type(value) is type(text) and value == text
    assert result["ENABLE_LLM_INFERENCE"] is False



def hold_save_reply(page):
    page.evaluate("""() => {
        const original = window.fetch;
        window.saveRequests = 0;
        window.fetch = async (...args) => {
            const response = await original(...args);
            if (String(args[0]) === '/api/settings/commit') {
                window.saveRequests++;
                if (window.saveRequests === 1) {
                    await new Promise((resolve, reject) => {
                        window.releaseSaveReply = resolve;
                        window.loseSaveReply = () => reject(new Error('simulated lost reply'));
                    });
                }
            }
            return response;
        };
    }""")


def release_save_reply(page, lose=False):
    page.evaluate('() => { ' + ('loseSaveReply' if lose else 'releaseSaveReply') + '(); }')
    page.wait_for_function('!window.settingsSavePending')


@pytest.mark.parametrize('later', ['82', '90'])
def test_later_edit_survives_save_acknowledgment(open_page, tmp_path, later):
    page = open_page({})
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '81')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    assert saved(tmp_path)['JPEG_QUALITY'] == 81
    assert page.locator('#btn-apply').is_disabled()
    assert page.locator('#btn-discard').is_disabled()
    assert page.locator('#btn-start').is_disabled()
    page.fill('[name="JPEG_QUALITY"]', later)
    page.evaluate('() => { commitGlobalDraft(); discardGlobalDraft(); }')
    assert page.locator('[name="JPEG_QUALITY"]').input_value() == later
    release_save_reply(page)
    assert page.evaluate('window.saveRequests') == 1
    assert page.locator('[name="JPEG_QUALITY"]').input_value() == later
    assert page.evaluate('window.draftState.JPEG_QUALITY') == later
    assert page.evaluate('window.originalState.JPEG_QUALITY') == 81
    assert page.locator('#btn-apply').is_enabled()
    assert page.locator('#btn-start').is_disabled()
    apply(page)
    assert saved(tmp_path)['JPEG_QUALITY'] == int(later)
    assert page.locator('#btn-start').is_enabled()


def test_failed_apply_keeps_later_correction(open_page, tmp_path):
    page = open_page({})
    before = (tmp_path / 'settings.json').read_bytes()
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '500')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    page.fill('[name="JPEG_QUALITY"]', '82')
    release_save_reply(page)
    assert (tmp_path / 'settings.json').read_bytes() == before
    assert page.locator('[name="JPEG_QUALITY"]').input_value() == '82'
    assert page.evaluate('window.draftState.JPEG_QUALITY') == '82'
    assert page.locator('#btn-apply').is_enabled()
    apply(page)
    assert saved(tmp_path)['JPEG_QUALITY'] == 82


def test_lost_reply_cannot_claim_old_values_are_saved(open_page, tmp_path):
    page = open_page({})
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '81')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    page.fill('[name="JPEG_QUALITY"]', '90')
    release_save_reply(page, lose=True)
    assert saved(tmp_path)['JPEG_QUALITY'] == 81
    assert page.locator('#btn-start').is_disabled()
    assert page.locator('#saved-status-label').is_hidden()
    assert page.locator('#btn-apply').is_enabled()
    assert page.locator('#btn-discard').is_disabled()
    if page.locator('#modal-overlay').is_visible():
        page.click('#modal-ok')
    assert page.locator('[data-notice-id="save"]').is_visible()
    page.evaluate('discardGlobalDraft()')
    assert page.locator('#btn-start').is_disabled()
    apply(page)
    assert saved(tmp_path)['JPEG_QUALITY'] == 90
    assert not page.evaluate('window.settingsSaveUnconfirmed')
    assert page.locator('[name="JPEG_QUALITY"]').input_value() == str(saved(tmp_path)['JPEG_QUALITY'])
    assert page.locator('#btn-start').is_enabled()


def test_later_token_edit_is_not_cleared_by_old_save_reply(open_page, tmp_path):
    import config_loader
    page = open_page(ready_ai())
    hold_save_reply(page)
    page.evaluate("window.switchTab('ai')")
    token = page.locator('#prov_token_custom')
    token.fill('fake-submitted-token')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    token.fill('fake-later-token')
    release_save_reply(page)
    assert config_loader._manager.get_env_tokens()['CUSTOM_TOKEN'] == 'fake-submitted-token'
    assert token.input_value() == 'fake-later-token'
    assert page.evaluate('window.draftState["ENV_TOKENS.custom"]') == 'fake-later-token'
    apply(page)
    assert config_loader._manager.get_env_tokens()['CUSTOM_TOKEN'] == 'fake-later-token'
    assert token.input_value() == ''
    assert 'ENV_TOKENS' not in saved(tmp_path)


def test_stop_remains_available_during_settings_save(open_page):
    page = open_page({})
    hold_save_reply(page)
    page.evaluate("window.runState = {...window.runState, phase: 'running'}; "
                  "setButtonState(document.getElementById('btn-stop'), true)")
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '81')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    assert page.locator('#btn-stop').is_enabled()
    assert page.locator('#btn-start').is_disabled()
    release_save_reply(page)
    assert page.locator('#btn-stop').is_enabled()



def test_later_prompt_edit_keeps_its_mode_when_disk_mode_changed(open_page, tmp_path):
    page = open_page({**ready_ai(), 'LLM_USER_PROMPT': 'original'})
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('file prompt', encoding='utf-8')
    raw = saved(tmp_path)
    raw.update(LLM_USER_PROMPT_MODE='FILE', LLM_USER_PROMPT=str(prompt))
    (tmp_path / 'settings.json').write_text(json.dumps(raw), encoding='utf-8')
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '81')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    page.evaluate("window.switchTab('ai')")
    page.fill('#user_text_input', 'later text')
    release_save_reply(page)
    assert page.locator('#user_prompt_mode').input_value() == 'TEXT'
    assert page.locator('#user_text_input').input_value() == 'later text'
    assert page.locator('#user_text_input').is_enabled()
    assert saved(tmp_path)['LLM_USER_PROMPT_MODE'] == 'FILE'
    apply(page)
    assert saved(tmp_path)['LLM_USER_PROMPT_MODE'] == 'TEXT'
    assert saved(tmp_path)['LLM_USER_PROMPT'] == 'later text'


def test_discard_clears_same_display_repair_intent(open_page):
    page = open_page({**ready_ai(), 'LLM_USER_PROMPT': {'a': 1}})
    page.evaluate("window.switchTab('ai')")
    page.fill('#user_text_input', 'temporary')
    page.fill('#user_text_input', '{"a": 1}')
    assert page.evaluate('window.hasUnsavedEdits()')
    page.click('#btn-discard')
    assert not page.evaluate('window.hasUnsavedEdits()')
    assert page.locator('#btn-start').is_disabled()
    assert page.evaluate("'LLM_USER_PROMPT' in window.diskErrors")


from pathlib import Path
LOCALES = sorted(p.stem for p in (Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json'))


@pytest.mark.parametrize('locale', LOCALES)
def test_save_lifecycle_status_is_translated(open_page, tmp_path, locale):
    page = open_page({})
    page.set_viewport_size({'width': 1280, 'height': 800})
    page.evaluate('changeLanguage', locale)
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '81')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    locale_path = Path(__file__).resolve().parents[2] / 'src/locales' / f'{locale}.json'
    messages = json.loads(locale_path.read_text(encoding='utf-8'))
    assert page.locator('#unsaved-warning-label span').inner_text() == messages['settings_saving']
    assert page.locator('#saved-status-label').is_hidden()
    page.screenshot(path=str(tmp_path / f'saving-{locale}.png'))
    release_save_reply(page, lose=True)
    assert page.locator('#modal-message').inner_text() == messages['err_settings_save_unconfirmed']
    page.screenshot(path=str(tmp_path / f'unconfirmed-dialog-{locale}.png'))
    page.click('#modal-ok')
    assert page.locator('#unsaved-warning-label span').inner_text() == messages['settings_save_unconfirmed']
    assert page.locator('#saved-status-label').is_hidden()
    page.screenshot(path=str(tmp_path / f'unconfirmed-{locale}.png'))
    print(f'Settings lifecycle visual evidence: {tmp_path}')
    apply(page)
    assert page.locator('#saved-status-label').is_visible()


@pytest.mark.parametrize('suppress_repair_metadata', [False, True])
def test_original_string_repair_assertion_still_detects_missing_provenance(
        open_page, tmp_path, monkeypatch, suppress_repair_metadata):
    import routes.web_server as web
    if suppress_repair_metadata:
        monkeypatch.setattr(web, 'settings_form_repair_fields', lambda _: [])
    def check():
        test_explicit_text_repair_is_not_confused_with_invalid_stored_type(
            open_page, tmp_path, 'LLM_USER_PROMPT', '#user_text_input', {'a': 1}, '{"a": 1}', False)
    if suppress_repair_metadata:
        with pytest.raises(AssertionError, match='valid string repair'):
            check()
    else:
        check()


@pytest.mark.parametrize('omit_control_sync', [False, True])
def test_original_stale_control_assertion_still_detects_missing_sync(
        open_page, tmp_path, monkeypatch, omit_control_sync):
    from flask import Flask
    original = Flask.send_static_file
    if omit_control_sync:
        def serve(self, filename):
            response = original(self, filename)
            if filename == 'app.js':
                response.direct_passthrough = False
                source = response.get_data(as_text=True)
                assert source.count('syncDraftControls(late);') == 1
                response.set_data(source.replace('syncDraftControls(late);', '/* independent negative control */'))
            return response
        monkeypatch.setattr(Flask, 'send_static_file', serve)
    def check():
        test_apply_reconciles_untouched_controls_with_fresh_disk_values(
            open_page, tmp_path, 'MAX_DIMENSION', 1920, 1234, '[name="MAX_DIMENSION"]', 'output')
    if omit_control_sync:
        with pytest.raises(AssertionError, match='clean controls'):
            check()
    else:
        check()


def hold_operation_reply(page, path):
    page.evaluate(
        """path => {
        const original = window.fetch;
        window.fetch = async (...args) => {
            const response = await original(...args);
            if (String(args[0]) === path && !window.operationHeld) {
                window.operationHeld = true;
                await new Promise(resolve => { window.releaseOperation = resolve; });
            }
            return response;
        };
    }""",
        path,
    )


WIPED = '!Object.hasOwn(window.rawOriginalState.ENV_TOKENS, "custom")'


@pytest.mark.parametrize('wipe_before_reply', [False, True], ids=['ordered-control', 'delayed-apply-reply'])
def test_save_reply_cannot_restore_a_wiped_token_view(open_page, tmp_path, wipe_before_reply):
    import config_loader
    page = open_page(ready_ai(), tokens={'custom': 'sk-fake0123456789abcdef0123456789abcdef'})
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '81')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    if not wipe_before_reply:
        release_save_reply(page)
    page.evaluate("window.switchTab('ai')")
    page.locator('#provider-custom button[onclick*="wipeToken"]').click()
    with page.expect_response('**/api/settings/wipe_token') as reply:
        page.click('#modal-ok')
    assert reply.value.status == 200
    page.wait_for_function(WIPED)
    assert config_loader._manager.get_env_tokens() == {}
    if wipe_before_reply:
        release_save_reply(page)
    observed = page.evaluate(f"""() => ({{
        wiped: {WIPED},
        placeholder: document.getElementById('prov_token_custom').placeholder
    }})""")
    assert observed['wiped'] and observed['placeholder'] != '********', observed
    assert saved(tmp_path)['JPEG_QUALITY'] == 81


@pytest.mark.parametrize("delayed", [False, True])
def test_token_edit_after_wipe_request_survives_reply(open_page, tmp_path, delayed):
    import config_loader

    page = open_page(ready_ai(), tokens={"custom": "sk-fake0123456789abcdef0123456789abcdef"})
    page.evaluate("window.switchTab('ai')")
    if delayed:
        hold_operation_reply(page, "/api/settings/wipe_token")
    page.locator('#provider-custom button[onclick*="wipeToken"]').click()
    page.click("#modal-ok")
    if delayed:
        page.wait_for_function('typeof releaseOperation === "function"')
    else:
        page.wait_for_function(WIPED)
    replacement = "sk-fake0123456789abcdef0123456789"
    page.fill("#prov_token_custom", replacement)
    if delayed:
        page.evaluate("() => { releaseOperation(); }")
        page.wait_for_function(WIPED)
    assert page.locator("#prov_token_custom").input_value() == replacement, "later token edit must survive"
    assert page.evaluate('window.draftState["ENV_TOKENS.custom"]') == replacement
    assert page.locator("#btn-apply").is_enabled()
    assert not config_loader._manager.get_env_tokens()
    apply(page)
    assert config_loader._manager.get_env_tokens()["CUSTOM_TOKEN"] == replacement
    assert "ENV_TOKENS" not in saved(tmp_path)


@pytest.mark.parametrize("fault", ["before-send", "lost", "malformed", "fatal"])
@pytest.mark.parametrize("later", [False, True])
def test_retry_resolves_original_submission_before_later_edits(open_page, tmp_path, fault, later):
    page = open_page({})
    page.evaluate(
        """fault => {
        const original = window.fetch;
        let first = true;
        window.fetch = async (...args) => {
            if (String(args[0]) !== '/api/settings/commit' || !first) return original(...args);
            first = false;
            if (fault === 'before-send') throw new Error('isolated failure before request');
            const response = await original(...args);
            if (fault === 'lost') throw new Error('isolated lost response');
            return new Response(fault === 'malformed' ? '{broken' : JSON.stringify({status:'fatal'}),
                {status:500, headers:{'Content-Type':'application/json'}});
        };
    }""",
        fault,
    )
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', "81")
    page.click("#btn-apply")
    page.wait_for_function("window.settingsSaveUnconfirmed && !window.settingsSavePending")
    assert saved(tmp_path).get("JPEG_QUALITY", 90) == (90 if fault == "before-send" else 81)
    assert page.locator("#btn-start").is_disabled() and page.locator("#btn-discard").is_disabled()
    page.click("#modal-ok")
    if later:
        page.fill('[name="JPEG_QUALITY"]', "82")
    apply(page)
    assert saved(tmp_path)["JPEG_QUALITY"] == (82 if later else 81)
    assert not page.evaluate("window.settingsSaveUnconfirmed")


@pytest.mark.parametrize("later", [False, True])
def test_wipe_after_unconfirmed_save_does_not_resave_old_token(open_page, tmp_path, later):
    import config_loader

    page = open_page(ready_ai())
    hold_save_reply(page)
    page.evaluate("window.switchTab('ai')")
    page.fill("#prov_token_custom", "fake-submitted")
    page.click("#btn-apply")
    page.wait_for_function('typeof releaseSaveReply === "function"')
    release_save_reply(page, lose=True)
    page.click("#modal-ok")
    page.locator('#provider-custom button[onclick*="wipeToken"]').click()
    with page.expect_response("**/api/settings/wipe_token"):
        page.click("#modal-ok")
    page.wait_for_function('!Object.hasOwn(window.draftState, "ENV_TOKENS.custom")')
    if later:
        page.fill("#prov_token_custom", "fake-new-explicit")
    apply(page)
    assert config_loader._manager.get_env_tokens() == ({"CUSTOM_TOKEN": "fake-new-explicit"} if later else {})
    assert "ENV_TOKENS" not in saved(tmp_path)


def test_old_rejected_apply_cannot_replace_reset_recovery(open_page, tmp_path):
    page = open_page({}, raw_settings="{broken")
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', "81")
    page.click("#btn-apply")
    page.wait_for_function('typeof releaseSaveReply === "function"')
    page.locator('#fatal-corrupted-instructions button[onclick="resetSettings()"]').click()
    page.wait_for_function("window.diskErrors.general.value === 'error_settings_reset'")
    release_save_reply(page)
    assert page.locator("#fatal-reset-instructions").is_visible()
    assert page.evaluate("window.diskErrors.general.value") == "error_settings_reset"
    assert saved(tmp_path)["JPEG_QUALITY"] == 90


def test_old_reset_reply_cannot_replace_later_apply(open_page, tmp_path):
    page = open_page({}, raw_settings="{broken")
    hold_operation_reply(page, "/api/settings/reset")
    page.locator('#fatal-corrupted-instructions button[onclick="resetSettings()"]').click()
    page.wait_for_function('typeof releaseOperation === "function"')
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', "81")
    page.click("#btn-apply")
    page.wait_for_function('!window.settingsSavePending && window.diskErrors.general.value === "error_settings_reset"')
    apply(page)
    page.evaluate("() => { releaseOperation(); }")
    page.wait_for_function("!window.settingsSavePending")
    assert saved(tmp_path)["JPEG_QUALITY"] == 81
    assert page.locator("#fatal-reset-instructions").is_hidden()
    assert page.evaluate("window.diskErrors") == {}


@pytest.mark.parametrize("operation", ["wipe_token", "reset"])
def test_old_failed_operation_cannot_replace_newer_success(open_page, monkeypatch, operation):
    import routes.settings_api as api

    page = open_page({}, tokens={"custom": "fake-stored-token"})
    hold_operation_reply(page, "/api/settings/" + operation)
    target = "update_env_tokens" if operation == "wipe_token" else "save_settings"
    original = getattr(api, target)

    def fail_once(*args, **kwargs):
        raise OSError("isolated operation failure")

    monkeypatch.setattr(api, target, fail_once)
    if operation == "wipe_token":
        page.evaluate("() => { wipeToken('custom'); }")
        page.click("#modal-ok")
    else:
        page.evaluate("resetSettings()")
    page.wait_for_function('typeof releaseOperation === "function"')
    monkeypatch.setattr(api, target, original)
    if operation == "wipe_token":
        page.evaluate("() => { wipeToken('custom'); }")
        page.click("#modal-ok")
        page.wait_for_function('!Object.hasOwn(window.rawOriginalState.ENV_TOKENS, "custom")')
    else:
        page.evaluate("resetSettings()")
        page.wait_for_function("window.diskErrors.general?.value === 'error_settings_reset'")
    page.evaluate("() => { releaseOperation(); }")
    page.evaluate("async () => { await new Promise(resolve => setTimeout(resolve, 0)); }")
    owner = "wipe-custom" if operation == "wipe_token" else "reset"
    assert not page.evaluate("owner => window.notices.has(owner)", owner)


def test_token_wipe_preserves_unrelated_rejected_draft_errors(open_page):
    page = open_page({}, tokens={"custom": "fake-stored-token"})
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', "500")
    page.click("#btn-apply")
    page.wait_for_function('!window.settingsSavePending && window.applyErrors && "JPEG_QUALITY" in window.applyErrors')
    page.evaluate("() => { wipeToken('custom'); }")
    page.click("#modal-ok")
    page.wait_for_function('!Object.hasOwn(window.rawOriginalState.ENV_TOKENS, "custom")')
    assert page.evaluate('"JPEG_QUALITY" in window.applyErrors')
    assert page.locator('[name="JPEG_QUALITY"]').input_value() == "500"
    assert page.locator("#btn-apply").is_enabled()


@pytest.mark.parametrize("locale", LOCALES)
def test_operation_conflict_and_unknown_receipt_are_translated(open_page, tmp_path, locale):
    page = open_page({})
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate("changeLanguage", locale)
    page.evaluate("""async () => {
        const response = await fetch('/api/settings/commit', {method:'POST',
            headers:{'Content-Type':'application/json'},body:JSON.stringify({edits:{JPEG_QUALITY:80}})});
        if (!response.ok) throw new Error('fixture save failed');
    }""")
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', "81")
    page.click("#btn-apply")
    page.wait_for_function('!window.settingsSavePending && window.notices.has("save")')
    notice = page.locator('[data-notice-id="save"]')
    assert page.evaluate('getT("err_settings_changed_retry")') in notice.inner_text()
    assert notice.evaluate("(el) => el.scrollWidth <= el.clientWidth")
    assert notice.evaluate("""el => {
        const span = el.querySelector('span');
        const before = el.getBoundingClientRect().height;
        span.style.whiteSpace = 'nowrap';
        const single = el.getBoundingClientRect().height;
        span.style.whiteSpace = '';
        return before <= single + 1;
    }""")
    page.screenshot(path=str(tmp_path / f"operation-conflict-{locale}.png"))
    apply(page)
    page.evaluate("window.SETTINGS_EPOCH = 'different-process'")
    page.fill('[name="JPEG_QUALITY"]', "82")
    page.click("#btn-apply")
    page.wait_for_function("window.settingsSaveUnconfirmed && !window.settingsSavePending")
    assert page.locator("#btn-start").is_disabled()
    assert page.locator('[name="JPEG_QUALITY"]').input_value() == "82"
    assert page.evaluate('getT("err_settings_operation_unknown")') in page.locator("#modal-overlay").inner_text()
    assert saved(tmp_path)["JPEG_QUALITY"] == 81
    page.screenshot(path=str(tmp_path / f"operation-unknown-{locale}.png"))
    print(f"Operation visual evidence: {tmp_path}")


def test_retry_after_ai_is_disabled_preserves_normalized_submitted_value(open_page, tmp_path):
    page = open_page(
        ready_ai(max_tokens=8)
    )
    hold_save_reply(page)
    page.evaluate("window.switchTab('ai')")
    page.fill('[name="LLM_PROVIDERS.custom.max_tokens"]', "9")
    page.click("#btn-apply")
    page.wait_for_function('typeof releaseSaveReply === "function"')
    page.evaluate("window.switchTab('general')")
    page.select_option('#ENABLE_LLM_INFERENCE', 'false')
    release_save_reply(page, lose=True)
    page.click("#modal-ok")
    apply(page)
    result = saved(tmp_path)
    assert result["ENABLE_LLM_INFERENCE"] is False
    assert result["LLM_PROVIDERS"]["custom"] == {**READY_ENTRY, "max_tokens": 9}
    assert type(result["LLM_PROVIDERS"]["custom"]["max_tokens"]) is int


@pytest.mark.parametrize("first_valid", [False, True])
@pytest.mark.parametrize("later_valid", [False, True])
def test_confirmed_retry_rejection_is_not_reported_as_saved_or_uncertain(open_page, tmp_path, first_valid, later_valid):
    page = open_page({})
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', "81" if first_valid else "500")
    page.click("#btn-apply")
    page.wait_for_function('typeof releaseSaveReply === "function"')
    release_save_reply(page, lose=True)
    page.click("#modal-ok")
    page.fill('[name="JPEG_QUALITY"]', "82" if later_valid else "600")
    page.click("#btn-apply")
    page.wait_for_function("!window.settingsSavePending && !window.settingsSaveUnconfirmed")
    assert saved(tmp_path).get("JPEG_QUALITY", 90) == (82 if later_valid else (81 if first_valid else 90))
    assert not page.evaluate('window.notices.has("save")')
    if later_valid:
        assert page.locator("#btn-apply").is_disabled()
    else:
        assert page.evaluate('"JPEG_QUALITY" in window.applyErrors')
        assert page.locator("#btn-apply").is_enabled()
        assert page.locator("#saved-status-label").is_hidden()
        assert "toast-visible" not in (page.locator("#generic-toast").get_attribute("class") or "")
