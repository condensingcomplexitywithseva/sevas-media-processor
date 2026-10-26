# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from test_settings_preservation_acceptance_ui import (
    apply, hold_operation_reply, hold_save_reply, release_save_reply, saved,
    test_apply_reconciles_untouched_controls_with_fresh_disk_values as reconcile,
)


def test_apply_reconciles_an_untouched_provider_choice_with_fresh_disk_values(open_page, tmp_path):
    reconcile(open_page, tmp_path, "LLM_PROVIDER", "ollama", "lm-studio", "#LLM_PROVIDER", "ai")


@pytest.mark.parametrize("providers", [None, {"ollama": None}, {"ollama": []}])
def test_repairing_provider_refreshes_derived_defaults_in_all_controls(open_page, tmp_path, providers):
    page = open_page({"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "ollama", "LLM_PROVIDERS": providers})
    page.evaluate("window.switchTab('ai')")
    page.fill('[name="LLM_PROVIDERS.ollama.url"]', "http://127.0.0.1:9/no-request")
    data = apply(page)
    assert saved(tmp_path)["LLM_PROVIDERS"] == {"ollama": {"url": "http://127.0.0.1:9/no-request"}}
    view = data["settings"]["LLM_PROVIDERS"]["ollama"]
    for field in ("model", "max_tokens", "response_extraction_path"):
        assert page.locator(f'[name="LLM_PROVIDERS.ollama.{field}"]').input_value() == str(view[field]), field


def test_provider_switch_during_save_keeps_hidden_frame_clean(open_page, tmp_path):
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'ollama',
                      'LLM_PROVIDERS': {'ollama': {'model': 'old'}}})
    hold_save_reply(page)
    page.evaluate("window.switchTab('ai')")
    page.fill('[name="LLM_PROVIDERS.ollama.model"]', 'submitted')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    page.select_option('#LLM_PROVIDER', 'lm-studio')
    page.click('#modal-ok')
    release_save_reply(page)
    assert page.locator('#LLM_PROVIDER').input_value() == 'lm-studio'
    assert page.locator('#provider-lm-studio').is_visible()
    assert not page.locator('#provider-ollama .dirty-field').count()
    assert page.evaluate('window.draftState["LLM_PROVIDERS.ollama.model"]') == 'submitted'
    apply(page)
    assert saved(tmp_path)['LLM_PROVIDER'] == 'lm-studio'
    assert saved(tmp_path)['LLM_PROVIDERS']['ollama']['model'] == 'submitted'


def test_acknowledgment_refreshes_prompt_mode_and_provider_frame(open_page, tmp_path):
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'ollama', 'LLM_USER_PROMPT': 'old'})
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('file prompt', encoding='utf-8')
    raw = saved(tmp_path)
    raw.update(LLM_PROVIDER='lm-studio', LLM_USER_PROMPT_MODE='FILE', LLM_USER_PROMPT=str(prompt))
    (tmp_path / 'settings.json').write_text(json.dumps(raw), encoding='utf-8')
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '81')
    apply(page)
    page.evaluate("window.switchTab('ai')")
    assert page.locator('#LLM_PROVIDER').input_value() == 'lm-studio'
    assert page.locator('#provider-lm-studio').is_visible()
    assert page.locator('#provider-ollama').is_hidden()
    assert page.locator('#user_prompt_mode').input_value() == 'FILE'
    assert page.locator('#user_file_input').is_enabled()
    assert page.locator('#user_text_input').is_disabled()
    assert page.locator('#user_file_input').input_value() == str(prompt)
    page.wait_for_function("document.getElementById('user_file_preview').textContent.includes('file prompt')")
    page.select_option('#LLM_PROVIDER', 'ollama')
    assert not page.locator('#modal-overlay').is_visible()


def test_later_read_only_inspection_survives_acknowledgment(open_page):
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'ollama'})
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '81')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    page.evaluate("goToField('LLM_PROVIDERS.claude.model')")
    page.wait_for_function("document.activeElement.name === 'LLM_PROVIDERS.claude.model'")
    release_save_reply(page)
    assert page.locator('#provider-claude').get_attribute('data-provider-inspection') == 'true'
    assert page.locator('#provider-claude').is_visible()
    assert page.evaluate('document.activeElement.name') == 'LLM_PROVIDERS.claude.model'
    assert not page.evaluate('window.hasUnsavedEdits()')


@pytest.mark.parametrize('lose_reply', [False, True], ids=['confirmed-control', 'lost-reply'])
@pytest.mark.parametrize('switch_timing', ['before-reply', 'after-reply'])
def test_provider_discard_cannot_undo_submitted_save_on_retry(open_page, tmp_path, lose_reply, switch_timing):
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'ollama',
                      'LLM_PROVIDERS': {'ollama': {'model': 'old'}}})
    hold_save_reply(page)
    page.evaluate("window.switchTab('ai')")
    page.fill('[name="LLM_PROVIDERS.ollama.model"]', 'submitted')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    assert saved(tmp_path)['LLM_PROVIDERS']['ollama']['model'] == 'submitted'
    def switch():
        page.select_option('#LLM_PROVIDER', 'lm-studio')
        if page.locator('#modal-overlay').is_visible():
            page.click('#modal-ok')
    if switch_timing == 'before-reply':
        switch()
    release_save_reply(page, lose=lose_reply)
    if page.locator('#modal-overlay').is_visible():
        page.click('#modal-ok')
    if switch_timing == 'after-reply':
        switch()
    apply(page)
    result = saved(tmp_path)
    assert result['LLM_PROVIDER'] == 'lm-studio'
    assert result['LLM_PROVIDERS']['ollama']['model'] == 'submitted', (
        'provider-frame discard silently undid an already submitted save during retry')


@pytest.mark.parametrize('wipe_before_reply', [False, True], ids=['ordered-control', 'delayed-apply-reply'])
def test_save_reply_cannot_restore_a_wiped_token_view(open_page, tmp_path, wipe_before_reply):
    import config_loader
    page = open_page({'ENABLE_LLM_INFERENCE': True, 'LLM_PROVIDER': 'openai'},
                     tokens={'openai': 'sk-fake0123456789abcdef0123456789abcdef'})
    hold_save_reply(page)
    page.evaluate("window.switchTab('output')")
    page.fill('[name="JPEG_QUALITY"]', '81')
    page.click('#btn-apply')
    page.wait_for_function('typeof releaseSaveReply === "function"')
    if not wipe_before_reply:
        release_save_reply(page)
    page.evaluate("window.switchTab('ai')")
    page.locator('#provider-openai button[onclick*="wipeToken"]').click()
    with page.expect_response('**/api/settings/wipe_token') as reply:
        page.click('#modal-ok')
    assert reply.value.status == 200
    page.wait_for_function("'ENV_TOKENS.openai' in window.diskErrors")
    assert config_loader._manager.get_env_tokens() == {}
    if wipe_before_reply:
        release_save_reply(page)
    assert 'ENV_TOKENS.openai' in config_loader._manager.load_for_ui()[1]
    observed = page.evaluate("""() => ({
        missing: 'ENV_TOKENS.openai' in window.diskErrors,
        blocked: document.getElementById('btn-start').disabled,
        placeholder: document.getElementById('prov_token_openai').placeholder
    })""")
    assert observed['missing'] and observed['blocked'] and observed['placeholder'] != '********', observed


@pytest.mark.parametrize(
    "field,old,new",
    [
        ("model", "old", "submitted"),
        ("max_tokens", 8, 9),
        ("require_max_tokens", False, True),
    ],
)
@pytest.mark.parametrize("timing", ["before", "after"])
@pytest.mark.parametrize("lose", [False, True])
def test_provider_retry_preserves_complete_entry(open_page, tmp_path, field, old, new, timing, lose):
    opaque = {"a.b": {"empty": {}, "integer": 9007199254740993}, "boolean": "false"}
    page = open_page(
        {
            "ENABLE_LLM_INFERENCE": True,
            "LLM_PROVIDER": "ollama",
            "LLM_PROVIDERS": {"ollama": {field: old, "future": opaque}},
        }
    )
    hold_save_reply(page)
    page.evaluate("window.switchTab('ai')")
    selector = '[name="LLM_PROVIDERS.ollama.' + field + '"]'
    if isinstance(new, bool):
        page.locator(selector + '[type="checkbox"]').set_checked(new)
    else:
        page.fill(selector, str(new))
    page.click("#btn-apply")
    page.wait_for_function('typeof releaseSaveReply === "function"')
    expected = {field: new, "future": opaque}
    assert json.dumps(saved(tmp_path)["LLM_PROVIDERS"]["ollama"], sort_keys=True) == json.dumps(
        expected, sort_keys=True
    )

    def switch():
        page.select_option("#LLM_PROVIDER", "lm-studio")
        if page.locator("#modal-overlay").is_visible():
            page.click("#modal-ok")

    if timing == "before":
        switch()
    release_save_reply(page, lose=lose)
    if page.locator("#modal-overlay").is_visible():
        page.click("#modal-ok")
    if timing == "after":
        switch()
    requests = []
    page.on("request", lambda r: requests.append(r.post_data_json) if r.url.endswith("/api/settings/commit") else None)
    apply(page)
    assert json.dumps(saved(tmp_path)["LLM_PROVIDERS"]["ollama"], sort_keys=True) == json.dumps(
        expected, sort_keys=True
    )
    assert saved(tmp_path)["LLM_PROVIDER"] == "lm-studio"
    allowed = {"LLM_PROVIDER", "LLM_PROVIDERS.ollama." + field}
    assert all(set(r["edits"]) <= allowed for r in requests), requests


@pytest.mark.parametrize("delayed", [False, True])
def test_token_edit_after_wipe_request_survives_reply(open_page, tmp_path, delayed):
    import config_loader

    page = open_page(
        {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai"},
        tokens={"openai": "sk-fake0123456789abcdef0123456789abcdef"},
    )
    page.evaluate("window.switchTab('ai')")
    if delayed:
        hold_operation_reply(page, "/api/settings/wipe_token")
    page.locator('#provider-openai button[onclick*="wipeToken"]').click()
    page.click("#modal-ok")
    if delayed:
        page.wait_for_function('typeof releaseOperation === "function"')
    else:
        page.wait_for_function("'ENV_TOKENS.openai' in window.diskErrors")
    replacement = "sk-fake0123456789abcdef0123456789"
    page.fill("#prov_token_openai", replacement)
    if delayed:
        page.evaluate("() => { releaseOperation(); }")
        page.wait_for_function("'ENV_TOKENS.openai' in window.diskErrors")
    assert page.locator("#prov_token_openai").input_value() == replacement, "later token edit must survive"
    assert page.evaluate('window.draftState["ENV_TOKENS.openai"]') == replacement
    assert page.locator("#btn-apply").is_enabled()
    assert not config_loader._manager.get_env_tokens()
    apply(page)
    assert config_loader._manager.get_env_tokens()["OPENAI_TOKEN"] == replacement
    assert "ENV_TOKENS" not in saved(tmp_path)


def test_stale_token_view_does_not_bypass_actual_start_validation(open_page, monkeypatch):
    from routes import execution_api

    constructed = []

    def forbidden(*args, **kwargs):
        constructed.append(True)
        raise AssertionError("No processing may be constructed")

    monkeypatch.setattr(execution_api, "ProcessorCore", forbidden)
    page = open_page(
        {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "openai"},
        tokens={"openai": "sk-fake0123456789abcdef0123456789abcdef"},
    )
    page.evaluate("window.switchTab('ai')")
    page.locator('#provider-openai button[onclick*="wipeToken"]').click()
    page.click("#modal-ok")
    page.wait_for_function("'ENV_TOKENS.openai' in window.diskErrors")
    result = page.evaluate("""async () => {
        const response = await fetch('/api/process/start', {method:'POST'});
        return {status:response.status, body:await response.json()};
    }""")
    assert result["status"] == 400 and "ENV_TOKENS.openai" in result["body"]["errors"]
    assert not constructed


@pytest.mark.parametrize("fault", ["none", "discard", "ordering", "wipe-edit"])
def test_settings_interaction_oracles_detect_their_broken_counterparts(open_page, tmp_path, monkeypatch, fault):
    from flask import Flask

    changes = {
        "discard": (
            "const submitted = submission && Object.hasOwn(submission.edits, name)",
            "const submitted = false && submission && Object.hasOwn(submission.edits, name)",
        ),
        "ordering": ("if (snapshot.revision <= settingsRevision) return false;", "if (false) return false;"),
        "wipe-edit": ("if (fieldRevisions[name] === operation.revisions[name]) {", "if (true) {"),
    }
    if fault != "none":
        original = Flask.send_static_file

        def serve(app, filename):
            response = original(app, filename)
            if filename == "app.js":
                response.direct_passthrough = False
                source = response.get_data(as_text=True)
                old, new = changes[fault]
                assert source.count(old) == 1
                source = source.replace(old, new)
                if fault == "discard":
                    old_discard = "if (submission) submission.discarded.add(name);"
                    assert source.count(old_discard) == 1
                    source = source.replace(old_discard, "if (false) submission.discarded.add(name);")
                response.set_data(source)
            return response

        monkeypatch.setattr(Flask, "send_static_file", serve)

    def check():
        if fault == "discard":
            test_provider_retry_preserves_complete_entry(
                open_page, tmp_path, "model", "old", "submitted", "before", True
            )
        elif fault == "ordering":
            test_save_reply_cannot_restore_a_wiped_token_view(open_page, tmp_path, True)
        else:
            test_token_edit_after_wipe_request_survives_reply(open_page, tmp_path, True)

    if fault != "none":
        with pytest.raises(AssertionError):
            check()
    else:
        check()
