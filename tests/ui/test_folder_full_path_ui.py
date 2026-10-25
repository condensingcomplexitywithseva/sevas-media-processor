# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import os
from pathlib import Path

import pytest

RELATIVE = {"INPUT_FOLDER_PATH": "input", "OUTPUT_FOLDER_PATH": "output"}

RESOLVED_SPELLINGS = [
    "photos", "photos/2026", "photos\\2026\\", ".\\photos", "photos\\..\\scans",
    "..\\photos", "\\".join([".."] * 40 + ["deep"]), "\\photos", "/photos",
    "my photos, 2026", "a  b", "photos.", "photos ", " photos\t", "\u3000photos\u00a0",
    '"photos"', ' "my photos" ',
]
NOT_SHOWN = ["C:\\photos", "c:/photos", "\\\\server\\share\\photos", " C:\\photos",
             "\\\\?\\C:\\photos", "D:photos", "", "   ", "\t\n", '"C:\\my photos"', '""']


def folder_the_run_uses(value):
    from config_validator import Settings

    return str(Settings.model_validate({"INPUT_FOLDER_PATH": value}).INPUT_FOLDER_PATH.resolve())


def full_path_row(page, field):
    row = page.locator(f'[data-full-path-for="{field}"]')
    return row, row.locator("code")


def test_relative_default_folders_show_their_full_paths_with_copy(open_page):
    page = open_page(RELATIVE)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    for field, name in (("INPUT_FOLDER_PATH", "input"), ("OUTPUT_FOLDER_PATH", "output")):
        row, code = full_path_row(page, field)
        assert row.is_visible(), field
        assert code.inner_text() == str(Path(name).resolve())
        row.locator("button").click()
        page.wait_for_timeout(300)
        assert page.evaluate("navigator.clipboard.readText()") == str(Path(name).resolve())
    assert page.locator("#input_folder_path").input_value() == "input"


def test_absolute_folders_show_no_extra_line(open_page, tmp_path):
    page = open_page({})
    for field in ("INPUT_FOLDER_PATH", "OUTPUT_FOLDER_PATH"):
        row, _ = full_path_row(page, field)
        assert not row.is_visible(), field


def test_the_line_follows_edits_before_apply(open_page, tmp_path):
    page = open_page({})
    row, code = full_path_row(page, "INPUT_FOLDER_PATH")
    page.fill("#input_folder_path", "scans\\2026")
    assert row.is_visible()
    assert code.inner_text() == str(Path("scans\\2026").resolve())
    page.fill("#input_folder_path", str(tmp_path / "input"))
    assert not row.is_visible()
    page.click('[data-full-path-for="INPUT_FOLDER_PATH"] ~ .input-row .btn-default')
    assert page.locator("#input_folder_path").input_value() == "input"
    assert row.is_visible() and code.inner_text() == str(Path("input").resolve())


def test_the_label_is_translated(open_page):
    page = open_page(RELATIVE)
    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(300)
    label = page.locator('[data-full-path-for="INPUT_FOLDER_PATH"] [data-i18n="lbl_full_path"]')
    assert label.is_visible() and label.inner_text() == "Полный путь:"


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_the_page_resolves_relative_folders_like_the_run(open_page):
    page = open_page(RELATIVE)
    shown = page.evaluate("values => values.map(window.fullFolderPath)", RESOLVED_SPELLINGS)
    assert shown == [folder_the_run_uses(value) for value in RESOLVED_SPELLINGS]
    assert page.evaluate("values => values.map(window.fullFolderPath)", NOT_SHOWN) == [""] * len(NOT_SHOWN)


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_the_resolution_check_rejects_a_plain_join(open_page):
    page = open_page(RELATIVE)
    naive = page.evaluate("values => values.map(v => window.WORKING_FOLDER + '\\\\' + v)",
                          RESOLVED_SPELLINGS)
    expected = [folder_the_run_uses(value) for value in RESOLVED_SPELLINGS]
    wrong = {value for value, got, want in zip(RESOLVED_SPELLINGS, naive, expected, strict=True) if got != want}
    assert {"..\\photos", "\\photos", "/photos", "photos\\..\\scans", "photos ", " photos\t"} <= wrong


def test_the_page_trims_exactly_the_whitespace_python_trims(open_page):
    page = open_page(RELATIVE)
    page_space = page.evaluate("""() => {
        const found = [];
        for (let code = 0; code <= 0xFFFF; code++) {
            if (code >= 0xD800 && code <= 0xDFFF) continue;
            if (window.pythonStrip('a' + String.fromCharCode(code)) === 'a') found.push(code);
        }
        return found;
    }""")
    python_space = [code for code in range(0x10000) if not 0xD800 <= code <= 0xDFFF and chr(code).isspace()]
    assert page_space == python_space
    assert page.evaluate("window.pythonStrip('a\\ufeff')") != "a", "control: trim() would remove U+FEFF"


def test_the_page_reads_a_path_setting_like_the_backend(open_page):
    from fs_utils import path_setting_text

    values = ['"C:\\my data"', ' "C:\\my data" \n', '" output "', '""', '"', '"C:\\data',
              'a"b"', "\u3000\"x\"\u00a0", "plain", ""]
    page = open_page(RELATIVE)
    assert page.evaluate("values => values.map(window.pathSettingText)", values) == \
        [path_setting_text(value) for value in values]


def test_folder_pickers_open_in_the_folder_the_field_names(open_page, tmp_path):
    page = open_page(RELATIVE)
    page.evaluate("""() => {
        window.pickerStarts = [];
        window.pywebview = {api: {browse_folder: start => {
            window.pickerStarts.push(start);
            return Promise.resolve(start === 'input' ? null : 'C:\\\\chosen');
        }}};
    }""")
    page.click("[onclick*=\"'input_folder_path'\"].ui-browse-folder")
    page.fill("#output_folder_path", "results\\2026")
    page.click("[onclick*=\"'output_folder_path'\"].ui-browse-folder")
    page.wait_for_function("window.pickerStarts.length === 2")
    assert page.evaluate("window.pickerStarts") == ["input", "results\\2026"]
    page.wait_for_function("document.getElementById('output_folder_path').value === 'C:\\\\chosen'")
    assert not full_path_row(page, "OUTPUT_FOLDER_PATH")[0].is_visible()
