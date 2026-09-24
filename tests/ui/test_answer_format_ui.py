# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

import pytest

LANGUAGES = sorted(
    path.stem for path in (Path(__file__).resolve().parents[2] / "src/locales").glob("*.json")
)

AI_ON_LOCAL = {"ENABLE_LLM_INFERENCE": True, "LLM_PROVIDER": "ollama"}

NEW_AI_FIELDS = ("LLM_OUTPUT_MODE", "LLM_OUTPUT_COLUMNS",
                 "LLM_OUTPUT_COLUMN_INSTRUCTIONS", "LLM_JSON_MAX_ATTEMPTS",
                 "LLM_ABORT_ON_MALFORMED_JSON")
NEW_OUTPUT_FIELDS = ("OUTPUT_FILENAME_PREFIX_LENGTH",
                     "OUTPUT_FILENAME_TIMESTAMPS")


def test_answer_format_section_sits_inside_the_ai_wrapper_in_order(open_page):
    page = open_page({"ENABLE_LLM_INFERENCE": False})
    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    state = page.evaluate(
        """(fields) => {
            const wrapper = document.getElementById('ai-settings-wrapper');
            const inWrapper = Object.fromEntries(fields.map(f =>
                [f, wrapper.contains(document.getElementById(f))]));
            const before = (a, b) => !!(document.getElementById(a)
                .compareDocumentPosition(document.getElementById(b))
                & Node.DOCUMENT_POSITION_FOLLOWING);
            return {
                in_wrapper: inWrapper,
                user_then_format: before('user_prompt_mode', 'LLM_OUTPUT_MODE'),
                format_then_system: before('LLM_OUTPUT_COLUMN_INSTRUCTIONS', 'sys_prompt_mode'),
                greyed: getComputedStyle(wrapper).pointerEvents === 'none',
                columns_is_textarea: document.getElementById('LLM_OUTPUT_COLUMNS').tagName === 'TEXTAREA',
                mode_options: Array.from(document.getElementById('LLM_OUTPUT_MODE').options).map(o => o.value),
            };
        }""",
        list(NEW_AI_FIELDS),
    )
    assert all(state["in_wrapper"].values()), state["in_wrapper"]
    assert state["user_then_format"] and state["format_then_system"], \
        "owner-ruled order: User Prompt, Answer format, System Prompt"
    assert state["greyed"], "AI off: the new fields grey out with the tab"
    assert state["columns_is_textarea"], "the columns box is multi-line (owner ruling)"
    assert state["mode_options"] == ["table_per_file", "table_per_page"]


def test_one_name_per_line_applies_and_lands_verbatim(open_page, tmp_path):
    page = open_page(AI_ON_LOCAL)
    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    typed = "vendor\ntotal\ndate\n"
    page.fill("#LLM_OUTPUT_COLUMNS", typed)
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function("!window.settingsSavePending && document.getElementById('btn-apply').disabled")

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["LLM_OUTPUT_COLUMNS"] == typed, "the user's exact text is stored"
    assert not page.evaluate(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'")


def test_refused_columns_error_on_the_visible_field(open_page):
    page = open_page(AI_ON_LOCAL)
    page.evaluate("window.switchTab('general')")
    page.wait_for_timeout(300)
    page.evaluate("""() => {
        const el = document.getElementById('LLM_OUTPUT_COLUMNS');
        el.value = 'genre, page';
        el.dispatchEvent(new Event('input', { bubbles: true }));
    }""")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function(
        "getComputedStyle(document.getElementById('global-error-banner')).display !== 'none'")

    assert page.evaluate("Object.keys(window.draftErrors)") == ["LLM_OUTPUT_COLUMNS"]
    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    field = page.evaluate("""() => {
        const el = document.getElementById('LLM_OUTPUT_COLUMNS');
        return {
            visible: el.offsetParent !== null,
            highlighted: el.classList.contains('error-field'),
            message: document.getElementById('err-LLM_OUTPUT_COLUMNS').textContent.trim(),
        };
    }""")
    assert field["visible"] and field["highlighted"]
    assert field["message"], "the translated err_output_columns_reserved text renders under the box"


def test_default_button_restores_the_shipped_columns(open_page):
    page = open_page(AI_ON_LOCAL | {"LLM_OUTPUT_COLUMNS": "genre, date"})
    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    page.evaluate("""() => document.getElementById('LLM_OUTPUT_COLUMNS')
        .parentElement.querySelector('button.btn-default').click()""")
    page.wait_for_timeout(200)
    state = page.evaluate("""() => ({
        value: document.getElementById('LLM_OUTPUT_COLUMNS').value,
        dirty: document.getElementById('LLM_OUTPUT_COLUMNS').classList.contains('dirty-field'),
        apply_enabled: !document.getElementById('btn-apply').disabled,
    })""")
    assert state == {"value": "answer", "dirty": True, "apply_enabled": True}


def test_output_tab_holds_the_naming_controls(open_page):
    page = open_page({})
    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)
    state = page.evaluate(
        """(fields) => {
            const form = document.getElementById('output-settings-form');
            return {
                in_form: Object.fromEntries(fields.map(f =>
                    [f, form.contains(document.getElementById(f))])),
                checkboxes: fields.filter(f => document.getElementById(f).type === 'checkbox'),
                tab_label: document.querySelector('[data-i18n="tab_output"]').textContent.trim(),
                header: document.querySelector('[data-i18n="hdr_output"]').textContent.trim(),
            };
        }""",
        list(NEW_OUTPUT_FIELDS),
    )
    assert all(state["in_form"].values()), state["in_form"]
    assert state["checkboxes"] == ["OUTPUT_FILENAME_TIMESTAMPS"]
    assert state["tab_label"] == "Output" and state["header"] == "Output"

    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(300)
    assert page.evaluate(
        "document.querySelector('[data-i18n=\"tab_output\"]').textContent.trim()") == "Результат"


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("ai_enabled", [True, False], ids=["ai_on", "ai_off"])
def test_retired_export_toggle_is_absent_and_an_output_edit_still_saves(
    open_page, tmp_path, language, ai_enabled,
):
    page = open_page({
        "ENABLE_LLM_INFERENCE": ai_enabled,
        "LLM_PROVIDER": "ollama",
        "EXPORT_JOINED_SHEET": {"obsolete": "invalid"},
        "FUTURE_OUTPUT_OPTION": {"keep": [1, False]},
    })
    original = (tmp_path / "settings.json").read_bytes()
    page.evaluate("language => changeLanguage(language)", language)
    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)
    assert page.locator('[name="EXPORT_JOINED_SHEET"]').count() == 0
    assert page.locator('[data-i18n="lbl_joined_sheet"], [data-i18n="hint_joined_sheet"]').count() == 0
    assert page.locator("#btn-apply").is_disabled()
    assert (tmp_path / "settings.json").read_bytes() == original
    page.fill("#OUTPUT_FILENAME_PREFIX_LENGTH", "17")
    page.wait_for_timeout(200)
    page.click("#btn-apply")
    page.wait_for_function("!window.settingsSavePending && document.getElementById('btn-apply').disabled")
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert "EXPORT_JOINED_SHEET" not in saved
    assert saved["OUTPUT_FILENAME_PREFIX_LENGTH"] == 17
    assert saved["FUTURE_OUTPUT_OPTION"] == {"keep": [1, False]}
