# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import pytest

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


def test_every_ui_test_has_a_hang_deadline(request):
    marker = request.node.get_closest_marker("timeout")
    assert request.config.pluginmanager.hasplugin("timeout"), "pytest-timeout is not active"
    assert marker is not None and marker.args == (180,)


def test_a_dead_browser_is_relaunched_for_the_next_page(_browser_keeper, open_page):
    before = _browser_keeper.relaunches
    _browser_keeper.live().close()
    assert not _browser_keeper.browser.is_connected()

    page = open_page({})
    assert _browser_keeper.relaunches == before + 1
    assert _browser_keeper.browser.is_connected()
    assert shell_state(page, "general")["visible_ids"] == ["tab-content-general"]


def test_a_crashed_page_open_is_retried_on_a_fresh_browser(_browser_keeper, open_page, monkeypatch):
    from playwright.sync_api import Error as PlaywrightError

    browser_type = type(_browser_keeper.live())
    original_new_page = browser_type.new_page
    calls = {"n": 0}

    def crash_once(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PlaywrightError("Browser.new_page: Target crashed")
        return original_new_page(self, *args, **kwargs)

    monkeypatch.setattr(browser_type, "new_page", crash_once)
    before = _browser_keeper.relaunches

    page = open_page({})

    assert calls["n"] == 2, "exactly one retry"
    assert _browser_keeper.relaunches == before + 1
    assert _browser_keeper.browser.is_connected()
    assert shell_state(page, "general")["visible_ids"] == ["tab-content-general"]


UI_ROLE_REFERENCE = {
    'heading': ('.tab-main-header', {'fontSize': '19.2px', 'fontWeight': '700', 'color': 'rgb(0, 85, 153)'}),
    'section': ('.section-header', {'fontWeight': '700', 'color': 'rgb(30, 41, 59)'}),
    'label': ('.form-group label', {'fontSize': '15.2px', 'fontWeight': '600', 'color': 'rgb(30, 41, 59)'}),
    'hint': ('.hint', {'fontSize': '12.8px', 'lineHeight': '17.92px', 'color': 'rgb(100, 116, 139)'}),
    'error': ('[data-field-error]', {'fontSize': '14.4px', 'fontWeight': '700', 'color': 'rgb(211, 47, 47)'}),
    'dialog': ('#modal-message', {'fontSize': '15.68px', 'lineHeight': '23.52px', 'color': 'rgb(30, 41, 59)'}),
    'location': ('#modal-path', {'fontSize': '11.7px', 'fontFamily': 'monospace'}),
    'button': ('.btn-default', {'fontWeight': '700', 'borderRadius': '8px'}),
    'console': ('#console-output', {'fontSize': '13.6px', 'fontFamily': 'Consolas, monospace'}),
}


def assert_role_reference(page):
    for role, (selector, expected) in UI_ROLE_REFERENCE.items():
        actual = page.locator(selector).first.evaluate(
            '(el, keys) => Object.fromEntries(keys.map(key => [key, getComputedStyle(el)[key]]))', list(expected))
        assert actual == expected, (role, actual, expected)


def test_approved_roles_and_shared_component_gallery(open_page, tmp_path):
    from pathlib import Path
    page = open_page({})
    page.set_viewport_size({'width': 1280, 'height': 800})
    locales = sorted(p.stem for p in (Path(__file__).resolve().parents[2] / 'src/locales').glob('*.json'))
    for language in locales:
        page.evaluate('changeLanguage', language)
        assert_role_reference(page)
        page.evaluate("""() => {
            notice({id: 'gallery-error', key: 'notice_log_failed'});
            notice({id: 'gallery-warning', key: 'notice_action_failed', level: 'warning'});
            notice({surface: 'toast', key: 'toast_saved'});
        }""")
        assert page.locator('[data-notice-id]').count() == 2
        page.screenshot(path=str(tmp_path / ('notice-gallery-' + language + '.png')))
        page.evaluate(r"""() => { notice({surface: 'dialog', key: 'notice_action_failed',
            detail: 'Long explanation. '.repeat(300),
            path: 'C:\\Reports\\' + 'long folder\\'.repeat(30) + 'report.xlsx'}); }""")
        page.locator('#modal-ok').scroll_into_view_if_needed()
        rect = page.locator('#modal-ok').bounding_box()
        assert rect and rect['y'] >= 0 and rect['y'] + rect['height'] <= 800
        assert page.locator('#modal-detail').evaluate("el => getComputedStyle(el).fontFamily") != 'monospace'
        page.screenshot(path=str(tmp_path / ('dialog-gallery-' + language + '.png')))
        page.click('#modal-ok')
        page.evaluate("clearNotice('gallery-error'); clearNotice('gallery-warning')")
    for language in locales:
        page.evaluate('changeLanguage', language)
        page.evaluate('exportDatabase()')
        page.wait_for_selector('#modal-overlay', state='visible')
        assert page.locator('#modal-message a[data-setting="OUTPUT_FOLDER_PATH"]').count() == 1
        assert page.locator('#modal-path').inner_text() == ''
        page.screenshot(path=str(tmp_path / ('export-refusal-gallery-' + language + '.png')))
        page.click('#modal-ok')
        page.evaluate("clearNotice('export')")


    for language in locales:
        page.evaluate('changeLanguage', language)
        page.evaluate("goToField('LLM_PROVIDERS.openai.max_tokens')")
        page.wait_for_function("document.activeElement.name === 'LLM_PROVIDERS.openai.max_tokens'")
        assert page.locator('[data-notice-id="provider-inspection"]').is_visible()
        assert page.locator('#max_tok_openai').evaluate('el => el.readOnly')
        assert_role_reference(page)
        page.screenshot(path=str(tmp_path / ('inspection-gallery-' + language + '.png')))
        page.locator('[data-notice-id="provider-inspection"] button').click()


def test_style_reference_detects_shared_and_private_changes(open_page):
    import pytest
    page = open_page({})
    assert_role_reference(page)
    sheet = page.add_style_tag(content=':root { --ui-hint-size: 30px; }')
    with pytest.raises(AssertionError):
        assert_role_reference(page)
    sheet.evaluate('el => el.remove()')
    target = page.locator('.hint').first
    target.evaluate("el => el.style.setProperty('font-size', '31px', 'important')")
    with pytest.raises(AssertionError):
        assert_role_reference(page)
    target.evaluate("el => el.style.removeProperty('font-size')")
    assert_role_reference(page)


def test_notice_lifetime_duplicates_translation_and_owned_clearing(open_page):
    page = open_page({})
    page.evaluate("""() => {
        notice({id: 'logs', key: 'notice_log_failed'});
        notice({id: 'logs', key: 'notice_log_failed'});
        const marker = document.createElement('span');
        marker.id = 'unrelated-error'; marker.className = 'error-text'; marker.textContent = 'Keep me';
        document.body.appendChild(marker);
        renderErrors();
    }""")
    assert page.locator('[data-notice-id="logs"]').count() == 1
    page.wait_for_timeout(3200)
    assert page.locator('[data-notice-id="logs"]').is_visible()
    page.evaluate("changeLanguage('ru')")
    assert 'Системный журнал' in page.locator('[data-notice-id="logs"]').inner_text()
    assert page.locator('#unrelated-error').inner_text() == 'Keep me'
    page.evaluate("notice({surface: 'toast', key: 'toast_saved'})")
    page.evaluate("changeLanguage('en')")
    assert page.locator('#generic-toast').inner_text() == 'Settings saved'


def test_notice_contract_rejects_prose_paths_and_error_toasts(open_page):
    import pytest
    from playwright.sync_api import Error
    page = open_page({})
    for expression in (
        "() => { appAlert('Failure', {path: 'Access was denied'}); }",
        "() => { notice({surface: 'toast', key: 'notice_action_failed', level: 'error'}); }",
        "() => { notice({key: 'notice_action_failed'}); }",
    ):
        with pytest.raises(Error):
            page.evaluate(expression)


def assert_export_composition(page):
    result = page.evaluate("""() => {
        const headline = document.getElementById('export-db-status');
        const cause = document.getElementById('export-db-reason');
        const action = document.getElementById('export-db-actions');
        const h = headline.getBoundingClientRect(), c = cause.getBoundingClientRect();
        const a = action.getBoundingClientRect();
        return {errorRole: headline.classList.contains('export-error'), causeRole: cause.classList.contains('hint'),
            headlineBeforeCause: h.bottom <= c.top + 1, causeBeforeActions: c.bottom <= a.top + 1};
    }""")
    assert all(result.values()), result


def test_composition_controls_detect_default_prose_and_overlap(open_page):
    import pytest
    page = open_page({})
    page.evaluate("""() => {
        switchTab('exports');
        showExportOutcome({status: 'error', saved: [],
            failed: [{format: 'xlsx', report: 'Workbook', category: 'permission', error: 'Denied'}],
            recovery_id: 'fixture'});
    }""")
    assert_export_composition(page)
    page.locator('#export-db-status').evaluate("el => el.className = ''")
    with pytest.raises(AssertionError):
        assert_export_composition(page)
    page.locator('#export-db-status').evaluate("el => el.className = 'export-error'")
    page.locator('#export-db-actions').evaluate("el => {el.style.position = 'absolute'; el.style.top = '0';}")
    with pytest.raises(AssertionError):
        assert_export_composition(page)


def test_lifetime_control_detects_unrelated_clear(open_page):
    import pytest
    page = open_page({})
    page.evaluate("notice({id:'lifetime', key:'notice_action_failed'})")
    def check():
        page.evaluate("renderErrors()")
        assert page.locator('[data-notice-id="lifetime"]').is_visible()
    check()
    page.evaluate("window.renderErrors = () => document.getElementById('persistent-notices').replaceChildren()")
    with pytest.raises(AssertionError):
        check()


def inline_appearance(source):
    import re
    from html.parser import HTMLParser
    found = []
    class Styles(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name != 'style':
                    continue
                value = re.sub(r'{%.*?%}', '', value or '')
                for declaration in value.split(';'):
                    if declaration.strip() and declaration.partition(':')[0].strip() != 'display':
                        found.append((tag, declaration.strip()))
    Styles().feed(source)
    return found


def test_no_private_inline_appearance():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / 'src'
    for path in sorted((root / 'templates').rglob('*.html')):
        source = path.read_text(encoding='utf-8')
        assert not inline_appearance(source), (path, inline_appearance(source))


def test_inline_appearance_positive_controls():
    assert inline_appearance('<span style="font-size:30px">Bad</span>')
    assert not inline_appearance('<span style="display:none">Hidden</span>')


@pytest.mark.parametrize('bypass_drain', [False, True])
def test_fixture_drain_waits_for_accepted_requests(app_server, server_drains, monkeypatch, bypass_drain):
    import threading
    import requests

    url = app_server({})
    drain = server_drains[0]
    original = drain.server.app
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def delayed_request(environ, start_response):
        if environ['PATH_INFO'] != '/drain-probe':
            return original(environ, start_response)
        entered.set()
        if not release.wait(5):
            raise RuntimeError('test did not release delayed request')
        start_response('200 OK', [('Content-Type', 'text/plain')])
        return [b'finished']

    drain.server.app = delayed_request

    def request():
        try:
            assert requests.get(url + '/drain-probe', timeout=6).text == 'finished'
        except Exception as exc:
            errors.append(exc)

    def close():
        try:
            drain.close()
        except Exception as exc:
            errors.append(exc)
        finally:
            closed.set()

    waiting = threading.Event()
    wait_for_requests = drain.wait_for_requests
    def wait():
        waiting.set()
        if not bypass_drain:
            wait_for_requests()
    monkeypatch.setattr(drain, 'wait_for_requests', wait)
    client = threading.Thread(target=request)
    closer = threading.Thread(target=close)
    client.start()
    try:
        assert entered.wait(3)
        closer.start()
        assert waiting.wait(3)
        assert closed.wait(3 if bypass_drain else 0.05) == bypass_drain
    finally:
        release.set()
        client.join(timeout=7)
        if closer.ident is not None:
            closer.join(timeout=7)
    assert closed.is_set() and not client.is_alive() and not closer.is_alive()
    assert not errors
