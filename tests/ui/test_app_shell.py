# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


TABS = ["general", "images", "docs", "animations", "videos", "output", "ai", "exports"]


def shell_state(page, tab):
    return page.evaluate(
        """(tab) => {
            const panes = Array.from(document.querySelectorAll('.tab-pane'));
            const visible = panes.filter(p => getComputedStyle(p).display !== 'none');
            const link = document.querySelector(`.sidebar-tab[data-tab="${tab}"]`);
            return {
                visible_ids: visible.map(p => p.id),
                link_active: !!(link && link.classList.contains('active')),
                header_key: document.getElementById('tab-header-text').getAttribute('data-i18n'),
            };
        }""",
        tab,
    )


def dirty_state(page):
    return page.evaluate(
        """() => ({
            apply_disabled: document.getElementById('btn-apply').disabled,
            discard_disabled: document.getElementById('btn-discard').disabled,
            unsaved_visible: getComputedStyle(document.getElementById('unsaved-warning-label')).display !== 'none',
            input_value: document.querySelector('input[name="INPUT_FOLDER_PATH"]').value,
            input_dirty: document.querySelector('input[name="INPUT_FOLDER_PATH"]').classList.contains('dirty-field'),
        })"""
    )


def test_all_tabs_switch_client_side(open_page):
    page = open_page({})
    for tab in TABS:
        page.click(f'.sidebar-tab[data-tab="{tab}"]')
        page.wait_for_timeout(400)
        state = shell_state(page, tab)
        assert state["visible_ids"] == [f"tab-content-{tab}"], \
            f"exactly the {tab} pane must be visible, got {state['visible_ids']}"
        assert state["link_active"], f"sidebar link for {tab} must be active"
        assert state["header_key"] == f"hdr_{tab}"


def test_edit_then_discard_round_trip(open_page):
    page = open_page({})
    original = dirty_state(page)
    assert original["apply_disabled"] and original["discard_disabled"]
    assert not original["unsaved_visible"]

    page.fill('input[name="INPUT_FOLDER_PATH"]', original["input_value"] + "_edited")
    page.wait_for_timeout(200)

    edited = dirty_state(page)
    assert not edited["apply_disabled"], "Apply must enable after an edit"
    assert not edited["discard_disabled"], "Discard must enable after an edit"
    assert edited["unsaved_visible"], "unsaved-changes warning must show"
    assert edited["input_dirty"], "edited field must be marked dirty"

    page.click("#btn-discard")
    page.wait_for_timeout(200)

    reverted = dirty_state(page)
    assert reverted["apply_disabled"] and reverted["discard_disabled"]
    assert not reverted["unsaved_visible"]
    assert reverted["input_value"] == original["input_value"], "Discard must restore the field text"
    assert not reverted["input_dirty"]


def test_duplicate_tab_routes_are_gone(open_page):
    from routes.web_server import SESSION_TOKEN

    page = open_page({})
    paths = ["/", "/videos", "/general", "/ai"]

    ungated = page.evaluate(
        """async paths => {
            const out = {};
            for (const path of paths) out[path] = (await fetch(path)).status;
            return out;
        }""",
        paths,
    )
    assert ungated["/"] == 200
    for path in paths[1:]:
        assert ungated[path] == 403, f"{path} answered {ungated[path]} unauthenticated"

    authenticated = page.evaluate(
        """async ({paths, token}) => {
            const out = {};
            for (const path of paths) {
                const r = await fetch(path, {headers: {'X-App-Token': token}});
                out[path] = r.status;
            }
            return out;
        }""",
        {"paths": paths, "token": SESSION_TOKEN},
    )
    assert authenticated["/"] == 200
    for path in paths[1:]:
        assert authenticated[path] == 404, (
            f"{path} is a live page route again (got {authenticated[path]})"
        )


def test_dead_code_stays_deleted(open_page):
    page = open_page({})
    assert page.evaluate("typeof applyDraftStateToDOM") == "undefined"
