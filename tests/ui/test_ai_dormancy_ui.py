# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import json
import pytest
from PIL import Image

RETRIES_FIELD = 'input[name="LLM_MAX_RETRIES"]'

GARBAGE_RETRIES = {"LLM_MAX_RETRIES": "abc"}


def test_ai_off_scalar_garbage_shows_nothing(open_page):
    page = open_page({"ENABLE_LLM_INFERENCE": False, **GARBAGE_RETRIES})
    state = page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            disk_errors: Object.keys(window.diskErrors || {}),
            start_disabled: document.getElementById('btn-start').disabled,
        })"""
    )
    assert not state["banner_visible"], "AI is off — a broken AI scalar must not banner"
    assert state["disk_errors"] == []
    assert not state["start_disabled"], "Start must not be blocked"


def test_toggling_ai_on_surfaces_scalar_garbage(open_page):
    page = open_page(
        {"ENABLE_LLM_INFERENCE": False, "LLM_PROVIDER": "openai", **GARBAGE_RETRIES},
        tokens={"openai": "sk-fake0123456789abcdef0123456789abcdef"},
    )
    assert not page.evaluate(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    ), "sanity: clean start while AI is off"

    page.select_option("#ENABLE_LLM_INFERENCE", "true")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'"
    )

    state = page.evaluate(
        """() => ({
            banner_visible: getComputedStyle(document.getElementById('global-error-banner')).display !== 'none',
            errors: Object.keys(window.draftErrors || {}),
        })"""
    )
    assert state["banner_visible"], "AI on: the dormant garbage must now block Apply"
    assert state["errors"] == ["LLM_MAX_RETRIES"], \
        "with a token present the ONLY error is the scalar under test"

    page.click('[data-tab="ai"]')
    page.wait_for_timeout(400)
    field = page.evaluate(
        f"""() => {{
        const el = document.querySelector('{RETRIES_FIELD}');
        return {{ visible: el.offsetParent !== null,
                  highlighted: el.classList.contains('error-field') }};
    }}"""
    )
    assert field["visible"] and field["highlighted"], \
        "the blocking field is on screen and highlighted — never hidden"



@pytest.mark.parametrize("providers", [None, {"ollama": {"max_tokens": "abc", "url": None}, "future": {"empty": {}}}])
def test_broken_ai_settings_survive_apply_and_a_complete_ai_off_run(open_page, tmp_path, monkeypatch, providers):
    import app_context

    def forbidden_ai_client(*args, **kwargs):
        raise AssertionError("An AI-off run must not construct an AI client")

    monkeypatch.setattr(app_context, "LLMClient", forbidden_ai_client)
    broken_ai = {
        "LLM_PROVIDER": "unsupported-provider", "LLM_PROVIDERS": providers,
        "LLM_USER_PROMPT": {"not_text": []}, "LLM_USER_PROMPT_MODE": "invalid-mode",
        "LLM_OUTPUT_MODE": ["not-a-mode"], "LLM_OUTPUT_COLUMNS": None,
        "LLM_MAX_RETRIES": "abc", "LLM_TIMEOUT_SECONDS": -1,
    }
    page = open_page({"ENABLE_LLM_INFERENCE": False, **broken_ai})
    assert page.evaluate("window.diskErrors") == {}
    assert page.locator("#btn-start").is_enabled()
    page.click('.sidebar-tab[data-tab="output"]')
    page.fill('[name="JPEG_QUALITY"]', "81")
    with page.expect_response("**/api/settings/commit") as reply:
        page.click("#btn-apply")
    assert reply.value.status == 200
    page.wait_for_function("!window.settingsSavePending && !window.hasUnsavedEdits()")
    assert page.locator("#btn-start").is_enabled()
    on_disk = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["JPEG_QUALITY"] == 81
    assert on_disk["ENABLE_LLM_INFERENCE"] is False
    assert json.dumps({key: on_disk[key] for key in broken_ai}, sort_keys=True) == json.dumps(broken_ai, sort_keys=True)
    saved_bytes = (tmp_path / "settings.json").read_bytes()
    Image.new("RGB", (64, 48), (30, 80, 120)).save(tmp_path / "input" / "dormant_probe.png")
    page.click("#btn-start")
    page.wait_for_function("""() => {
        const status = document.getElementById('run-status');
        return getComputedStyle(status).display !== 'none' && status.className.startsWith('status-');
    }""", timeout=30000)
    assert page.locator("#run-status").get_attribute("class") == "status-done"
    assert list((tmp_path / "output").rglob("*.jpg"))
    page.wait_for_function("""async () => {
        const response = await fetch('/api/process/status');
        return !(await response.json()).is_running;
    }""")
    assert (tmp_path / "settings.json").read_bytes() == saved_bytes
    assert not (tmp_path / ".env").exists()
