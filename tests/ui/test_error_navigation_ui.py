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
