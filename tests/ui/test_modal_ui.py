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
