# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json


def test_apply_commits_to_disk_and_resets_state(open_page, tmp_path):
    page = open_page({})

    page.evaluate("window.switchTab('output')")
    page.wait_for_timeout(400)
    page.fill('input[name="JPEG_QUALITY"]', "77")
    page.wait_for_timeout(200)
    assert page.evaluate("!document.getElementById('btn-apply').disabled"), \
        "Apply must enable for a real change"

    page.click("#btn-apply")
    page.wait_for_function("document.getElementById('btn-apply').disabled")

    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved.get("JPEG_QUALITY") == 77, \
        f"Apply must persist the normalized integer, got {saved.get('JPEG_QUALITY')!r}"

    state = page.evaluate(
        """() => ({
            apply_disabled: document.getElementById('btn-apply').disabled,
            saved_label_visible: getComputedStyle(document.getElementById('saved-status-label')).display !== 'none',
            field_dirty: document.querySelector('input[name="JPEG_QUALITY"]').classList.contains('dirty-field'),
        })"""
    )
    assert state["apply_disabled"], "Apply must disable after a successful commit"
    assert state["saved_label_visible"], "'Configuration Up to Date' must show again"
    assert not state["field_dirty"], "the committed field must lose its dirty marker"
