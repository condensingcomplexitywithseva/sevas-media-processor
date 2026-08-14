# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0



def _form_field_ids(page):
    return page.evaluate(
        """() => Array.from(
               document.querySelectorAll('#output-settings-form input')
           ).map(i => i.id)"""
    )


def test_output_tab_field_order_and_preamble(open_page):
    page = open_page({})

    first = page.evaluate(
        """() => {
            const el = document.querySelector('#output-settings-form')
                               .firstElementChild;
            const header = el.querySelector('[data-i18n="lbl_size_pipeline"]');
            const body = el.querySelector('[data-i18n="hint_size_pipeline"]');
            return {header_weight: header && getComputedStyle(header).fontWeight,
                    separator: getComputedStyle(el).borderBottomWidth,
                    lines: body ? body.querySelectorAll('.i18n-line').length : 0,
                    hung: body ? body.querySelectorAll('.i18n-hang').length : 0,
                    text: el.textContent};
        }"""
    )
    assert first["header_weight"] in ("700", "bold"), "preamble header is bold"
    assert first["separator"] == "1px", "preamble block ends with a separator line"
    assert first["lines"] == 4, "preamble renders each of its 4 lines as a block"
    assert first["hung"] == 3, "the three numbered steps hang-indent their wraps"
    assert "strongest lever" in first["text"]

    assert _form_field_ids(page) == [
        "MAX_DIMENSION",
        "JPEG_QUALITY",
        "LOWEST_QUALITY",
        "MAX_FILE_SIZE_KB",
        "WHITE_BACKGROUND",
        "PILLOW_MAX_PIXELS",
    ]
    inputs_before_advanced = page.evaluate(
        """() => {
            const header = document.querySelector(
                '#output-settings-form .section-header');
            let n = 0;
            for (const input of document.querySelectorAll(
                     '#output-settings-form input')) {
                if (header.compareDocumentPosition(input)
                        & Node.DOCUMENT_POSITION_PRECEDING) n++;
            }
            return n;
        }"""
    )
    assert inputs_before_advanced == 4


def test_size_field_labels_and_hints_in_both_languages(open_page):
    page = open_page({})

    def texts():
        return page.evaluate(
            """() => ({
                label: document.querySelector('[data-i18n="lbl_max_dim"]').textContent,
                max_dim: document.querySelector('[data-i18n="hint_max_dim"]').textContent,
                max_size: document.querySelector('[data-i18n="hint_max_size"]').textContent,
                lowest: document.querySelector('[data-i18n="hint_lowest_qual"]').textContent,
                pdf: document.querySelector('[data-i18n="hint_pdf_scale"]').textContent,
            })"""
        )

    en = texts()
    assert en["label"] == "Max Resolution (px):"
    assert "3840 (4K)" in en["max_dim"] and "2560 (QHD)" in en["max_dim"]
    assert "soft target" in en["max_size"]
    assert "Max Resolution" in en["max_size"], "size hint points at the real lever"
    assert "discolored" in en["lowest"] and "default of 20" in en["lowest"]
    assert "Text Sharpness 2 + Max Resolution 2560" in en["pdf"]
    assert "Text Sharpness 1 + Max Resolution 1920" in en["pdf"]
    assert "Output Quality" in en["pdf"]

    page.evaluate("changeLanguage('ru')")
    page.wait_for_timeout(300)
    ru = texts()
    assert ru["label"] == "Макс. разрешение (px):"
    assert "3840 (4K)" in ru["max_dim"] and "2560 (QHD)" in ru["max_dim"]
    assert "Мягкая цель" in ru["max_size"]
    assert "значение по умолчанию 20" in ru["lowest"]
    assert "Резкость текста 2 + Макс. разрешение 2560" in ru["pdf"]
    assert "Резкость текста 1 + Макс. разрешение 1920" in ru["pdf"]
    assert "Качество результата" in ru["pdf"], "PDF hint names the Output tab's visible RU name"
