# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import time
from pathlib import Path

from PIL import Image


def test_full_run_reaches_green_done_state(open_page, tmp_path):
    page = open_page({})
    Image.new("RGB", (320, 200), (200, 60, 60)).save(tmp_path / "input" / "probe.png")

    assert page.evaluate("!document.getElementById('btn-start').disabled"), \
        "Start must be enabled for a valid config"
    page.click("#btn-start")

    page.wait_for_function(
        """() => {
            const s = document.getElementById('run-status');
            return getComputedStyle(s).display !== 'none' && s.className.startsWith('status-');
        }""",
        timeout=30000,
    )
    page.wait_for_timeout(500)

    final = page.evaluate(
        """() => ({
            status_cls: document.getElementById('run-status').className,
            bar_cls: document.getElementById('progress-bar').className,
            bar_val: document.getElementById('progress-bar').value,
            start_enabled: !document.getElementById('btn-start').disabled,
            stop_disabled: document.getElementById('btn-stop').disabled,
        })"""
    )
    assert final["status_cls"] == "status-done", f"run must succeed, got {final}"
    assert "progress-done" in final["bar_cls"], "progress bar must turn green"
    assert int(final["bar_val"]) == 100, "progress must reach 100% only at the true end"
    assert final["start_enabled"], "Start must re-enable after the run"
    assert final["stop_disabled"], "Stop must disable after the run"

    jpegs = list((tmp_path / "output").rglob("*.jpg")) + list((tmp_path / "output").rglob("*.jpeg"))
    assert jpegs, "the pipeline must produce a JPEG in the sandboxed output folder"


def test_stop_mid_run_aborts_promptly_and_keeps_partial_output(open_page, tmp_path):
    page = open_page({})
    for i in range(80):
        Image.new("RGB", (800, 600), ((i * 7) % 256, 60, 120)).save(
            tmp_path / "input" / f"probe_{i:03d}.png"
        )

    page.click("#btn-start")
    page.wait_for_function("!document.getElementById('btn-stop').disabled")
    page.click("#btn-stop")
    clicked_at = time.monotonic()

    page.wait_for_function(
        """() => {
            const s = document.getElementById('run-status');
            return getComputedStyle(s).display !== 'none' && s.className.startsWith('status-');
        }""",
        timeout=15000,
    )
    reaction = time.monotonic() - clicked_at

    final = page.evaluate(
        """() => ({
            status_cls: document.getElementById('run-status').className,
            start_enabled: !document.getElementById('btn-start').disabled,
            stop_disabled: document.getElementById('btn-stop').disabled,
        })"""
    )
    assert final["status_cls"] == "status-aborted", \
        f"Stop must end the run as aborted, got {final}"
    assert final["start_enabled"], "Start must re-enable after an abort"
    assert final["stop_disabled"], "Stop must disarm after an abort"
    assert reaction < 5.0, f"abort took {reaction:.1f}s after the Stop click"

    jpegs = list((tmp_path / "output").rglob("*.jpg"))
    assert len(jpegs) < 80, "the abort must actually cut the run short"



def button_look(page, button_id="btn-start"):
    return page.evaluate(
        """id => {
            const b = document.getElementById(id);
            const s = getComputedStyle(b);
            return {
                disabled: b.disabled,
                visible: [s.opacity, s.backgroundColor, s.color],
                cursor: s.cursor,
                title: b.title,
            };
        }""",
        button_id,
    )


def test_clicking_start_immediately_changes_how_it_looks(open_page):
    page = open_page({})
    page.evaluate(
        """() => {
            const real = window.fetch;
            window.fetch = (url, opts) => url === '/api/process/start'
                ? new Promise(() => {})      // never settles
                : real(url, opts);
        }"""
    )

    before = button_look(page)
    assert before["disabled"] is False

    page.click("#btn-start")

    after = button_look(page)
    assert after["disabled"] is True, "Start was not disabled by the click"
    assert after["visible"] != before["visible"], (
        "Start is disabled but looks identical - nothing tells the user the "
        f"click registered (still {after['visible']})"
    )
    assert after["cursor"] == "not-allowed"
    assert after["title"], "a disabled Start should say why on hover"


def test_a_render_pass_cannot_hand_start_back_mid_run(open_page):
    page = open_page({})
    live = button_look(page)
    page.evaluate("() => { window.runState = {...window.runState, phase: 'running'}; window.updateGlobalControls(); }")

    mid = button_look(page)
    assert mid["disabled"] is True, "a render pass re-enabled Start during a run"
    assert mid["visible"] != live["visible"], "a disabled Start still looks live"
    assert mid["cursor"] == "not-allowed"

    page.evaluate("() => { window.runState = {...window.runState, phase: 'idle'}; window.updateGlobalControls(); }")
    after = button_look(page)
    assert after["disabled"] is False, "Start never came back after the run"
    assert after["visible"] == live["visible"], "Start came back looking wrong"
    assert after["cursor"] == "pointer"



import pytest


@pytest.mark.parametrize("outcome,progress", [("done", 100), ("aborted", 37)])
def test_a_late_start_acknowledgement_preserves_an_already_terminal_run(open_page, monkeypatch, outcome, progress):
    from routes import execution_api

    class ImmediateCore:
        def __init__(self, callback):
            self.callback = callback

        def run(self):
            self.callback({"type": "progress", "value": progress})
            self.callback({"type": outcome})

    monkeypatch.setattr(execution_api, "ProcessorCore", lambda *args, **kwargs: ImmediateCore(kwargs["on_progress"]))
    page = open_page({})
    page.evaluate("""() => {
        notice({id:'run', key:'run_status_failed', summaryKey:'notice_run_failed'});
        const finish = window.finishAutomaticExportRun;
        window.finishAutomaticExportRun = (...args) => {
            finish(...args);
            queueMicrotask(() => { window.startAckApplied = true; });
        };
        const original = window.fetch;
        window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (url !== '/api/process/start') return response;
            const text = await response.text();
            return new Promise(resolve => { window.releaseStartAck = () => resolve(
                new Response(text, {status:response.status, headers:{'Content-Type':'application/json'}})); });
        };
    }""")
    deadline = time.monotonic() + 5
    while not execution_api.global_broadcaster.listeners and time.monotonic() < deadline:
        page.wait_for_timeout(10)
    assert execution_api.global_broadcaster.listeners
    page.click("#btn-start")
    page.wait_for_function("outcome => document.getElementById('run-status').classList.contains('status-' + outcome)",
                           arg=outcome)
    page.wait_for_function("typeof window.releaseStartAck === 'function'")
    page.evaluate("window.releaseStartAck()")
    page.wait_for_function("window.startAckApplied === true")

    def check():
        assert page.evaluate("window.runActive") is False
        assert page.locator("#btn-stop").is_disabled()
        assert page.locator("#progress-bar").evaluate("el => el.value") == progress
        assert not page.locator('[data-notice-id="run"]').count()

    check()
    page.evaluate("""() => {
        setButtonState(document.getElementById('btn-stop'), true);
        document.getElementById('progress-bar').value = 0;
    }""")
    with pytest.raises(AssertionError):
        check()



@pytest.mark.parametrize("older_lost_reply", [False, True])
def test_start_reply_cannot_erase_observed_progress_or_supersede_a_new_start(open_page, older_lost_reply):
    from routes import execution_api
    page = open_page({})
    page.evaluate("""() => {
        const original = window.fetch;
        window.pendingStarts = [];
        window.fetch = (url, options) => url === '/api/process/start'
            ? new Promise((resolve, reject) => {
                window.pendingStarts.push({resolve, reject, id:options.headers["X-Run-Id"]}); })
            : original(url, options);
        window.releaseStart = async (index, lost) => {
            const pending = window.pendingStarts[index];
            if (lost) pending.reject(new TypeError('lost older Start reply'));
            else pending.resolve({json: async () => ({status:'success', export_barrier:index + 1,
                run_state:{run_id:pending.id, revision:index * 10 + 1,
                    phase:'running', progress:0, is_running:true}})});
            await new Promise(resolve => setTimeout(resolve, 0));
        };
    }""")
    deadline = time.monotonic() + 5
    while not execution_api.global_broadcaster.listeners and time.monotonic() < deadline:
        page.wait_for_timeout(10)
    assert execution_api.global_broadcaster.listeners
    page.click("#btn-start")
    if older_lost_reply:
        execution_api.global_broadcaster.emit({"type": "failed", "run_state": {
            "run_id": page.evaluate("window.runState.run_id"), "revision": 2,
            "phase": "failed", "progress": 0, "is_running": False}})
        page.wait_for_function("window.runActive === false")
        page.click("#btn-start")
    execution_api.global_broadcaster.emit({"type": "progress", "value": 37, "run_state": {
        "run_id": page.evaluate("window.runState.run_id"), "revision": 15 if older_lost_reply else 5,
        "phase": "running", "progress": 37, "is_running": True}})
    page.wait_for_function("document.getElementById('progress-bar').value === 37")
    current = 1 if older_lost_reply else 0
    page.evaluate("index => releaseStart(index, false)", current)
    assert page.locator("#progress-bar").evaluate("el => el.value") == 37
    if older_lost_reply:
        page.evaluate("releaseStart(0, true)")
    assert page.evaluate("window.runActive") is True
    assert page.locator("#btn-start").is_disabled()
    assert not page.locator("#btn-stop").is_disabled()
    assert not page.locator('[data-notice-id="start"]').count()


@pytest.mark.parametrize("reply", ["held", "lost", "acknowledged"])
def test_an_observed_running_worker_stays_stoppable_when_start_reply_is_delayed_or_lost(
        open_page, monkeypatch, reply):
    import threading
    from routes import execution_api
    release = threading.Event()

    class ControlledCore:
        def __init__(self, callback):
            self.callback = callback

        def run(self):
            self.callback({"type": "progress", "value": 37})
            release.wait(15)
            self.callback({"type": "aborted"})

    monkeypatch.setattr(execution_api, "ProcessorCore", lambda *args, **kwargs: ControlledCore(kwargs["on_progress"]))
    page = open_page({})
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (url !== '/api/process/start') return response;
            const body = await response.text();
            return new Promise((resolve, reject) => {
                window.releaseObservedStart = lost => {
                    if (lost) reject(new TypeError('lost Start response after worker launched'));
                    else resolve(new Response(body, {status:response.status,
                        headers:{'Content-Type':'application/json'}}));
                };
            });
        };
    }""")
    try:
        page.click("#btn-start")
        page.wait_for_function("typeof window.releaseObservedStart === 'function'")
        page.wait_for_function("document.getElementById('progress-bar').value === 37")
        if reply != "held":
            page.evaluate("async lost => { releaseObservedStart(lost); "
                          "await new Promise(resolve => setTimeout(resolve, 0)); }", reply == "lost")
        assert execution_api.run_controller.thread is not None and execution_api.run_controller.thread.is_alive()
        assert page.evaluate("window.runActive") is True, "a lost HTTP reply cannot contradict a live worker"
        assert page.locator("#btn-start").is_disabled()
        assert not page.locator("#btn-stop").is_disabled(), "progress proves a running worker which must be stoppable"
    finally:
        release.set()
        worker = execution_api.run_controller.thread
        if worker is not None:
            worker.join(5)
            assert not worker.is_alive()
        if reply == "held":
            page.evaluate("window.releaseObservedStart(false)")


@pytest.mark.parametrize("outcome", ["done", "aborted", "failed"])
@pytest.mark.parametrize("late_reply", [False, True])
def test_stop_acknowledgement_cannot_replace_a_terminal_run_state(open_page, monkeypatch, outcome, late_reply):
    import threading
    from routes import execution_api
    release = threading.Event()

    class ControlledCore:
        def __init__(self, callback):
            self.callback = callback

        def run(self):
            self.callback({"type": "progress", "value": 37})
            release.wait(15)
            self.callback({"type": outcome})

    monkeypatch.setattr(execution_api, "ProcessorCore", lambda *args, **kwargs: ControlledCore(kwargs["on_progress"]))
    page = open_page({})
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (url !== '/api/process/stop') return response;
            const body = await response.text();
            return new Promise(resolve => { window.releaseStopReply = () => resolve(
                new Response(body, {status:response.status, headers:{'Content-Type':'application/json'}})); });
        };
    }""")
    try:
        page.click("#btn-start")
        page.wait_for_function("!document.getElementById('btn-stop').disabled")
        page.click("#btn-stop")
        page.wait_for_function("typeof window.releaseStopReply === 'function'")
        assert execution_api.run_controller.abort.is_set(), "the actual Stop route must accept the request"
        if not late_reply:
            page.evaluate("releaseStopReply()")
            page.wait_for_function("document.getElementById('btn-stop').dataset.stopping === 'true'")
        release.set()
        page.wait_for_function(
            "outcome => document.getElementById('run-status').classList.contains('status-' + outcome)", arg=outcome)
        if late_reply:
            page.evaluate("async () => { releaseStopReply(); await new Promise(resolve => setTimeout(resolve, 0)); }")
        assert page.evaluate("window.runActive") is False
        assert page.locator("#btn-stop").is_disabled()
        assert page.locator("#btn-stop").get_attribute("data-stopping") == "false"
        assert page.locator("#btn-stop-icon").inner_text() == "🛑"
        assert not page.locator("#btn-start").is_disabled()
    finally:
        release.set()
        worker = execution_api.run_controller.thread
        if worker is not None:
            worker.join(5)
            assert not worker.is_alive()


@pytest.mark.parametrize("lost_reply", [False, True])
def test_an_old_stop_reply_cannot_change_a_new_running_worker(open_page, monkeypatch, lost_reply):
    import threading
    from routes import execution_api
    releases = [threading.Event(), threading.Event()]
    created = []

    class ControlledCore:
        def __init__(self, index, callback):
            self.index = index
            self.callback = callback

        def run(self):
            self.callback({"type": "progress", "value": 17 + self.index})
            releases[self.index].wait(15)
            self.callback({"type": "aborted"})

    def create(*args, **kwargs):
        core = ControlledCore(len(created), kwargs["on_progress"])
        created.append(core)
        return core

    monkeypatch.setattr(execution_api, "ProcessorCore", create)
    page = open_page({})
    page.evaluate("""() => {
        const original = window.fetch;
        window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (url !== '/api/process/stop') return response;
            const body = await response.text();
            return new Promise((resolve, reject) => { window.releaseOldStop = lost => {
                if (lost) reject(new TypeError('old Stop response lost'));
                else resolve(new Response(body, {status:response.status,
                    headers:{'Content-Type':'application/json'}}));
            }; });
        };
    }""")
    try:
        page.click("#btn-start")
        page.wait_for_function("!document.getElementById('btn-stop').disabled")
        page.click("#btn-stop")
        page.wait_for_function("typeof window.releaseOldStop === 'function'")
        old_worker = execution_api.run_controller.thread
        releases[0].set()
        assert old_worker is not None
        old_worker.join(5)
        assert not old_worker.is_alive()
        page.wait_for_function("window.runActive === false")
        page.click("#btn-start")
        page.wait_for_function("document.getElementById('progress-bar').value === 18 "
                               "&& !document.getElementById('btn-stop').disabled")
        page.evaluate("async lost => { releaseOldStop(lost); "
                      "await new Promise(resolve => setTimeout(resolve, 0)); }", lost_reply)
        assert len(created) == 2 and not execution_api.run_controller.abort.is_set()
        assert page.evaluate("window.runActive") is True
        assert page.locator("#btn-start").is_disabled()
        assert not page.locator("#btn-stop").is_disabled(), "the newer worker never received this old Stop"
        assert page.locator("#btn-stop").get_attribute("data-stopping") == "false"
        assert not page.locator('[data-notice-id="stop"]').count(), "an old transport error belongs to the old run"
    finally:
        for release in releases:
            release.set()
        worker = execution_api.run_controller.thread
        if worker is not None:
            worker.join(5)
            assert not worker.is_alive()


@pytest.mark.parametrize("outcome", ["done", "aborted", "failed"])
def test_reconnecting_stream_restores_a_missed_terminal_outcome(open_page, monkeypatch, outcome):
    import threading
    from routes import execution_api
    release = threading.Event()

    class Core:
        def __init__(self, settings, abort, on_progress):
            self.emit = on_progress

        def run(self):
            self.emit({"type": "progress", "value": 37})
            release.wait(10)
            self.emit({"type": outcome})
    monkeypatch.setattr(execution_api, "ProcessorCore", Core)
    page = open_page({})
    try:
        page.click("#btn-start")
        page.wait_for_function("document.getElementById('progress-bar').value === 37")
        old = page.evaluate("window.runState")
        page.evaluate("eventSource.close(); eventSource = null")
        release.set()
        assert execution_api.run_controller.thread is not None
        execution_api.run_controller.thread.join(5)
        assert page.evaluate("window.runActive")
        page.evaluate("attachSSEStream()")
        page.wait_for_function("outcome => window.runState.phase === outcome", arg=outcome)
        assert not page.evaluate("window.runActive")
        assert not page.locator("#btn-start").is_disabled()
        assert page.locator("#btn-stop").is_disabled()
        execution_api.global_broadcaster.emit({"type": "progress", "value": 37, "run_state": old})
        page.wait_for_timeout(100)
        assert page.evaluate("window.runState.phase") == outcome
    finally:
        release.set()
        assert execution_api.run_controller.thread is not None
        execution_api.run_controller.thread.join(5)


def test_lost_start_without_stream_delivery_reconciles_a_live_worker(open_page, monkeypatch):
    import threading
    from routes import execution_api
    release = threading.Event()

    class Core:
        def __init__(self, settings, abort, on_progress):
            self.abort = abort

        def run(self):
            release.wait(10)
    monkeypatch.setattr(execution_api, "ProcessorCore", Core)
    page = open_page({})
    page.evaluate("""() => {
        eventSource.close(); eventSource = null;
        const original = window.fetch;
        window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (url === '/api/process/start') throw new TypeError('lost acknowledgement');
            return response;
        };
    }""")
    try:
        page.click("#btn-start")
        page.wait_for_function("window.runState.phase === 'running'")
        assert page.evaluate("window.runActive")
        assert not page.locator("#btn-stop").is_disabled()
        page.click("#btn-stop")
        page.wait_for_function("window.runState.phase === 'stopping'")
        assert execution_api.run_controller.abort.is_set()
    finally:
        release.set()
        assert execution_api.run_controller.thread is not None
        execution_api.run_controller.thread.join(5)


@pytest.mark.parametrize("locale", sorted(
    path.stem for path in (Path(__file__).resolve().parents[2] / "src/locales").glob("*.json")))
def test_unconfirmed_start_keeps_controls_consistent_and_recovery_readable(open_page, tmp_path, locale):
    page = open_page({})
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate("locale => changeLanguage(locale)", locale)
    page.evaluate("""() => {
        eventSource.close(); eventSource = null;
        const original = window.fetch;
        window.fetch = (url, options) => ['/api/process/start','/api/process/status'].includes(url)
            ? Promise.reject(new TypeError('unconfirmed command fixture')) : original(url, options);
    }""")
    page.click("#btn-start")
    notice = page.locator('[data-notice-id="start"]')
    notice.wait_for(state="visible")
    assert page.evaluate("window.runState.phase") == "unknown"
    assert page.evaluate("window.runActive")
    assert page.locator("#btn-start").is_disabled()
    assert page.locator("#btn-stop").is_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator("#modal-ok").click()
    notice.locator('[data-notice-action="btn_details"]').click()
    if locale in ("en", "ru"):
        expected = "The application did not receive a reply" if locale == "en" else "Приложение не получило ответ"
        assert expected in page.locator("#modal-message").inner_text()
    assert not page.locator("#modal-detail").is_visible()
    assert not page.locator("#modal-detail-copy").is_visible()
    assert "TypeError" not in page.locator("#modal-message").inner_text()
    screenshot = tmp_path / f"unconfirmed-start-{locale}.png"
    page.screenshot(path=str(screenshot))
    print(f"RUN_CONTROL_SCREENSHOT {screenshot}")


@pytest.mark.parametrize("unrelated_dialog", [False, True])
def test_confirmed_start_clears_only_its_own_uncertainty_dialog(open_page, monkeypatch, unrelated_dialog):
    import threading
    from routes import execution_api
    release = threading.Event()
    class HeldCore:
        def run(self):
            release.wait(15)
    monkeypatch.setattr(execution_api, "ProcessorCore", lambda *args, **kwargs: HeldCore())
    page = open_page({})
    page.evaluate("""() => {
        eventSource.close(); eventSource = null;
        window.allowStatusRecovery = false;
        const original = window.fetch;
        window.fetch = async (url, options) => {
            if (url === '/api/process/status' && !window.allowStatusRecovery) throw new TypeError('status lost');
            const response = await original(url, options);
            if (url === '/api/process/start') throw new TypeError('reply lost after start');
            return response;
        };
    }""")
    try:
        page.click("#btn-start")
        page.locator("#modal-overlay").wait_for(state="visible")
        assert page.locator('[data-notice-id="start"]').is_visible()
        if unrelated_dialog:
            page.click("#modal-ok")
            page.evaluate("() => { appAlert({id:'other', text:'Other dialog'}); }")
        page.evaluate("() => { window.allowStatusRecovery = true; return reconcileRun(); }")
        page.wait_for_function("window.runState.phase === 'running'")
        assert not page.locator('[data-notice-id="start"]').count()
        assert page.locator("#modal-overlay").is_visible() is unrelated_dialog
        if unrelated_dialog:
            assert page.locator("#modal-message").inner_text() == "Other dialog"
        assert not page.locator("#btn-stop").is_disabled()
    finally:
        release.set()
        worker = execution_api.run_controller.thread
        assert worker is not None
        worker.join(5)
