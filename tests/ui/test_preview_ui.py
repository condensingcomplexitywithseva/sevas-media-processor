# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0



def open_ai_file_mode(open_page):
    page = open_page({})
    page.evaluate("window.switchTab('ai')")
    page.wait_for_timeout(400)
    page.select_option("#user_prompt_mode", "FILE")
    page.wait_for_timeout(300)
    return page


def load_preview_of(page, path):
    page.fill("#user_file_input", str(path))
    page.evaluate("window.loadPreview('user_file_input')")
    page.wait_for_timeout(600)
    return page.evaluate("document.getElementById('user_file_preview').innerText")


def test_non_txt_refusal_is_translated_and_carries_no_contents(open_page, tmp_path):
    secret = tmp_path / "win.ini"
    secret.write_text("[fonts]\nSECRET=value\n", encoding="utf-8")

    page = open_ai_file_mode(open_page)
    text = load_preview_of(page, secret)
    assert "Preview is available for .txt files only." in text
    assert "SECRET" not in text

    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(800)
    text = load_preview_of(page, secret)
    assert "Предпросмотр доступен только для файлов .txt." in text
    assert "SECRET" not in text


def test_unreadable_txt_shows_the_translated_key_not_an_exception(open_page, tmp_path):
    utf16 = tmp_path / "prompt.txt"
    utf16.write_bytes("привет".encode("utf-16"))

    page = open_ai_file_mode(open_page)
    text = load_preview_of(page, utf16)
    assert "The file could not be read as text." in text
    assert "codec" not in text
    assert "UnicodeDecodeError" not in text


def test_bomless_utf16_is_refused_not_rendered_as_spaced_garbage(open_page, tmp_path):
    bomless = tmp_path / "prompt.txt"
    bomless.write_bytes("must not render".encode("utf-16-le"))

    page = open_ai_file_mode(open_page)
    text = load_preview_of(page, bomless)
    assert "The file could not be read as text." in text
    assert "m u s t" not in text
    assert "must not render" not in text


def test_a_good_txt_file_previews_its_content(open_page, tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("extract the text please", encoding="utf-8")

    page = open_ai_file_mode(open_page)
    text = load_preview_of(page, prompt)
    assert "extract the text please" in text


def test_prompt_file_hint_is_permanently_visible_and_translated(open_page):
    page = open_ai_file_mode(open_page)

    state = page.evaluate(
        """() => {
            const grab = sel => {
                const el = document.querySelector(sel);
                return el ? { text: el.innerText,
                              visible: el.offsetParent !== null } : null;
            };
            return {
                user: grab('#user_file_container .hint'),
                sys:  grab('#sys_file_container .hint'),
            };
        }"""
    )
    assert state["user"] and state["user"]["visible"], state
    assert "UTF-8" in state["user"]["text"]
    assert "Notepad" in state["user"]["text"]
    assert state["sys"], "the system-prompt picker lost its hint"
    assert "UTF-8" in state["sys"]["text"]

    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(800)
    ru = page.evaluate(
        "document.querySelector('#user_file_container .hint').innerText"
    )
    assert "UTF-8" in ru
    assert "Блокноте" in ru, f"the hint did not switch to Russian: {ru!r}"
    assert "Сохранить как" in ru, "the RU hint must name RU Notepad's own menus"
