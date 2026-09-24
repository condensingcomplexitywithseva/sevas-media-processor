# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0



def modal_state(page):
    return page.evaluate(
        """() => ({
            visible: getComputedStyle(document.getElementById('modal-overlay')).display !== 'none',
            message: document.getElementById('modal-message').textContent,
        })"""
    )


def test_confirm_modal_renders_and_cancel_closes(open_page):
    page = open_page({})
    page.evaluate("() => { window.appConfirm('modal probe message'); }")
    page.wait_for_timeout(300)

    state = modal_state(page)
    assert state["visible"], "appConfirm must show the modal overlay"
    assert "modal probe message" in state["message"]

    page.click("#modal-cancel")
    page.wait_for_timeout(200)
    assert not modal_state(page)["visible"], "Cancel must close the modal"


def test_confirm_modal_resolves_true_on_ok(open_page):
    page = open_page({})
    page.evaluate(
        "() => { window._modalResult = 'pending';"
        " window.appConfirm('ok probe').then(v => { window._modalResult = v; }); }"
    )
    page.wait_for_timeout(300)
    page.click("#modal-ok")
    page.wait_for_timeout(200)
    assert page.evaluate("window._modalResult") is True
    assert not modal_state(page)["visible"], "OK must close the modal"



def test_modal_focus_is_contained_and_restored(open_page):
    page = open_page({})
    page.locator('#btn-start').focus()
    page.evaluate("() => { window.appAlert('Focus check'); }")
    for key in ('Tab', 'Shift+Tab', 'Tab'):
        page.keyboard.press(key)
        assert page.evaluate("!!document.activeElement.closest('#modal-dialog')")
    page.keyboard.press('Escape')
    assert page.evaluate('document.activeElement.id') == 'btn-start'
    page.evaluate('showAboutDialog()')
    for _ in range(8):
        page.keyboard.press('Tab')
        assert page.evaluate("!!document.activeElement.closest('#about-dialog')")
    page.keyboard.press('Escape')



def test_language_change_preserves_focus_on_a_modal_setting_link(open_page):
    page = open_page({})
    page.locator('#btn-start').focus()
    page.evaluate("""() => { appAlert({key: 'err_settings_invalid', field: 'MAX_DIMENSION'}); }""")
    page.locator('#modal-jump-link').focus()
    assert page.locator('#modal-jump-link').evaluate('(el) => el === document.activeElement')
    page.evaluate("changeLanguage('ru')")
    assert page.locator('#modal-jump-link').evaluate('(el) => el === document.activeElement'), (
        'Retranslation must not drop keyboard focus onto the inert page')
    page.keyboard.press('Enter')
    page.wait_for_timeout(400)
    assert page.locator('#MAX_DIMENSION').evaluate('(el) => el === document.activeElement')


def test_replacing_dialogs_settles_old_promises_and_restores_original_invoker(open_page):
    page = open_page({})
    page.locator('#btn-start').focus()
    page.evaluate("""() => {
        window.firstResult = 'pending'; window.secondResult = 'pending';
        appConfirm('First question').then(value => window.firstResult = value);
        appAlert('Second question').then(value => window.secondResult = value);
    }""")
    page.wait_for_function('window.firstResult === false')
    assert page.locator('#modal-message').inner_text() == 'Second question'
    assert page.evaluate('window.secondResult') == 'pending'
    page.evaluate('showAboutDialog()')
    page.wait_for_function('window.secondResult === false')
    assert not page.locator('#modal-overlay').is_visible()
    page.keyboard.press('Escape')
    assert not page.locator('#about-overlay').is_visible()
    assert page.locator('#btn-start').evaluate('(el) => el === document.activeElement')
    assert page.evaluate("[...document.body.children].every(el => !el.inert)")


def test_notice_focus_survives_ordinary_render(open_page):
    page = open_page({})
    page.evaluate("notice({id:'probe', key:'err_settings_invalid', field:'MAX_DIMENSION',"
                  " summaryKey:'notice_start_setting'})")
    link = page.locator('[data-notice-id="probe"] a')
    link.focus()
    page.evaluate('renderErrors()')
    assert link.evaluate('(el) => el === document.activeElement')


def test_dialog_returns_to_notice_invoker_after_render(open_page):
    page = open_page({})
    page.evaluate("notice({id:'probe', key:'err_settings_invalid', field:'MAX_DIMENSION'})")
    button = page.locator('[data-notice-id="probe"] button')
    button.click()
    page.evaluate('renderErrors()')
    page.click('#modal-ok')
    assert button.evaluate('(el) => el === document.activeElement')


def test_success_timer_cannot_clear_replacement_or_persistent_error(open_page):
    page = open_page({})
    page.clock.install()
    page.evaluate("notice({surface:'toast', key:'toast_saved'})")
    page.clock.fast_forward(2000)
    page.evaluate("notice({surface:'toast', key:'notice_action_failed'});"
                  " notice({id:'failure', key:'notice_action_failed'})")
    page.clock.fast_forward(1100)
    assert page.locator('#generic-toast').evaluate("el => el.classList.contains('toast-visible')")
    assert page.locator('[data-notice-id="failure"]').is_visible()
    page.clock.fast_forward(2000)
    assert not page.locator('#generic-toast').evaluate("el => el.classList.contains('toast-visible')")
    assert page.locator('[data-notice-id="failure"]').is_visible()


def test_repeated_setting_links_keep_the_focused_occurrence(open_page):
    page = open_page({})
    page.evaluate("""() => { appAlert({text:'{first} / {second}',
        refs:{first:'MAX_DIMENSION', second:'MAX_DIMENSION'}}); }""")
    second = page.locator('#modal-message a').nth(1)
    second.focus()
    page.evaluate("changeLanguage('ru')")
    assert second.evaluate('el => el === document.activeElement')
