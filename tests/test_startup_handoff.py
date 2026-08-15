# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import importlib.metadata
import importlib.util
import re
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import main as main_module


def blank_module(name: str) -> Any:
    return types.ModuleType(name)

FAKE_PORT = 45678
FAKE_SESSION_TOKEN = "fake-session-token-for-the-handoff-tests"
T = 5.0


class FakeEvent:

    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        self._handlers.append(handler)
        return self

    def fire(self):
        for handler in list(self._handlers):
            handler()


class FakeWindow:
    def __init__(self, title, **kwargs):
        self.title = title
        self.kwargs = kwargs
        self.events = types.SimpleNamespace(loaded=FakeEvent(), closing=FakeEvent())
        self.show_calls = 0
        self.destroy_calls = 0
        self.loaded_urls = []
        self.evaluated_js = []
        self.navigated = threading.Event()
        self.destroyed = threading.Event()

    def evaluate_js(self, script):
        self.evaluated_js.append(script)
        return True

    def show(self):
        if self.destroyed.is_set():
            raise RuntimeError(f"show() on destroyed window {self.title!r}")
        self.show_calls += 1

    def destroy(self):
        if self.destroyed.is_set():
            raise RuntimeError(f"destroy() twice on window {self.title!r}")
        self.destroy_calls += 1
        self.destroyed.set()

    def load_url(self, url):
        self.loaded_urls.append(url)
        self.navigated.set()


def fire_until(event, done, timeout=T):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event.fire()
        if done():
            return True
        time.sleep(0.01)
    return False


class StartupHarness:

    def __init__(self, monkeypatch):
        self.backend_gate = None
        self.create_app_error = None
        self.panic_calls = []
        self.webview = self._make_webview()

        monkeypatch.setitem(sys.modules, "webview", self.webview)
        monkeypatch.setitem(sys.modules, "config_loader", self._make_config_loader())
        routes_pkg, web_server = self._make_routes()
        monkeypatch.setitem(sys.modules, "routes", routes_pkg)
        monkeypatch.setitem(sys.modules, "routes.web_server", web_server)
        monkeypatch.setitem(sys.modules, "central_logger", self._make_central_logger())
        werkzeug_pkg, serving = self._make_werkzeug()
        monkeypatch.setitem(sys.modules, "werkzeug", werkzeug_pkg)
        monkeypatch.setitem(sys.modules, "werkzeug.serving", serving)

        monkeypatch.setattr(
            main_module, "write_fatal_panic_log", self.panic_calls.append
        )
        monkeypatch.setattr(main_module, "has_tkinter", False)

    def run(self, scenario):
        self.webview.scenario = scenario
        main_module.main()

    @property
    def windows(self):
        return self.webview.windows

    def _make_webview(self):
        mod = blank_module("webview")
        mod.windows = []
        mod.settings = {}
        mod.scenario = None
        mod.screens = [types.SimpleNamespace(x=0, y=0, width=1920, height=1200)]

        def create_window(title, **kwargs):
            window = FakeWindow(title, **kwargs)
            mod.windows.append(window)
            return window

        def start(**kwargs):
            if mod.scenario is not None:
                mod.scenario(*mod.windows)

        mod.create_window = create_window
        mod.start = start
        return mod

    def _make_config_loader(self):
        mod = blank_module("config_loader")
        mod.get_env_tokens = lambda: {}
        mod.log_settings_errors = lambda errors: None

        def load_for_ui():
            if self.backend_gate is not None:
                self.backend_gate.wait(timeout=T)
            return {"LOGGING_LEVEL": "INFO"}, []

        mod.load_for_ui = load_for_ui
        return mod

    def _make_routes(self):
        routes_pkg = blank_module("routes")
        web_server = blank_module("routes.web_server")

        def create_app():
            if self.create_app_error is not None:
                raise self.create_app_error
            return object()

        web_server.create_app = create_app
        web_server.SESSION_TOKEN = FAKE_SESSION_TOKEN
        routes_pkg.web_server = web_server
        return routes_pkg, web_server

    @staticmethod
    def _make_central_logger():
        mod = blank_module("central_logger")
        mod.setup_logging = lambda level, memory_buffer_handler=None: None
        mod.close_logging = lambda: None
        return mod

    @staticmethod
    def _make_werkzeug():
        werkzeug_pkg = blank_module("werkzeug")
        serving = blank_module("werkzeug.serving")

        class FakeServer:
            server_port = FAKE_PORT

            def serve_forever(self):
                pass

        serving.make_server = lambda host, port, app, threaded=False: FakeServer()
        werkzeug_pkg.serving = serving
        return werkzeug_pkg, serving


@pytest.fixture
def startup(monkeypatch):
    return StartupHarness(monkeypatch)


def test_happy_path_splash_shows_then_main_replaces_it(startup):
    seen = {}

    def scenario(splash, main_w):
        splash.events.loaded.fire()
        seen["splash_shown_on_loaded"] = splash.show_calls
        seen["backend_navigated"] = main_w.navigated.wait(T)
        seen["splash_destroyed_before_main_ready"] = splash.destroy_calls
        seen["main_shown"] = fire_until(
            main_w.events.loaded, lambda: main_w.show_calls >= 1
        )
        main_w.events.loaded.fire()
        seen["main_show_calls"] = main_w.show_calls
        seen["splash_destroy_calls"] = splash.destroy_calls

    startup.run(scenario)

    _splash, main_w = startup.windows
    assert seen["splash_shown_on_loaded"] == 1
    assert seen["backend_navigated"] is True
    assert main_w.loaded_urls == [f"http://127.0.0.1:{FAKE_PORT}"]
    assert seen["splash_destroyed_before_main_ready"] == 0, (
        "splash must survive until the main window is ready"
    )
    assert seen["main_shown"] is True
    assert seen["main_show_calls"] == 1, "main window must be shown exactly once"
    assert seen["splash_destroy_calls"] == 1, "splash must be destroyed exactly once"
    assert startup.panic_calls == []


def test_blank_page_loaded_event_never_reveals_main_window(startup):
    startup.backend_gate = threading.Event()
    seen = {}

    def scenario(splash, main_w):
        main_w.events.loaded.fire()
        seen["main_shown_for_blank_page"] = main_w.show_calls
        seen["splash_destroyed_for_blank_page"] = splash.destroy_calls
        splash.events.loaded.fire()
        seen["splash_shown"] = splash.show_calls
        startup.backend_gate.set()
        seen["backend_navigated"] = main_w.navigated.wait(T)
        seen["main_shown"] = fire_until(
            main_w.events.loaded, lambda: main_w.show_calls >= 1
        )
        seen["splash_destroy_calls"] = splash.destroy_calls

    startup.run(scenario)

    assert seen["main_shown_for_blank_page"] == 0, (
        "blank page 'loaded' must NOT reveal the main window"
    )
    assert seen["splash_destroyed_for_blank_page"] == 0, (
        "blank page 'loaded' must NOT tear down the splash"
    )
    assert seen["splash_shown"] == 1
    assert seen["backend_navigated"] is True
    assert seen["main_shown"] is True
    assert seen["splash_destroy_calls"] == 1


def test_stray_splash_loaded_events_are_ignored(startup):
    seen = {}

    def scenario(splash, main_w):
        splash.events.loaded.fire()
        splash.events.loaded.fire()
        seen["show_after_duplicate"] = splash.show_calls
        main_w.navigated.wait(T)
        seen["handoff_done"] = fire_until(
            main_w.events.loaded, lambda: splash.destroy_calls >= 1
        )
        splash.events.loaded.fire()
        seen["show_after_destroy"] = splash.show_calls
        seen["destroy_calls"] = splash.destroy_calls

    startup.run(scenario)

    assert seen["show_after_duplicate"] == 1, "splash must be shown exactly once"
    assert seen["handoff_done"] is True
    assert seen["show_after_destroy"] == 1, (
        "a late splash 'loaded' event must not re-show a destroyed splash"
    )
    assert seen["destroy_calls"] == 1


def test_backend_crash_tears_down_both_windows_and_reports(startup):
    startup.create_app_error = RuntimeError("boom during create_app")
    seen = {}

    def scenario(splash, main_w):
        seen["splash_torn_down"] = splash.destroyed.wait(T)
        seen["main_torn_down"] = main_w.destroyed.wait(T)

    with pytest.raises(RuntimeError, match="Fatal Startup Error"):
        startup.run(scenario)

    _splash, main_w = startup.windows
    assert seen["splash_torn_down"] is True
    assert seen["main_torn_down"] is True
    assert main_w.show_calls == 0, "a crashed boot must never reveal the main window"
    assert len(startup.panic_calls) == 1, "exactly one crash log must be written"
    assert "boom during create_app" in startup.panic_calls[0]


def test_both_windows_carry_their_load_bearing_creation_options(startup):
    startup.run(lambda splash, main_w: None)
    splash, main_w = startup.windows

    assert splash.kwargs.get("screen") is startup.webview.screens[0], (
        "the splash no longer names its screen; pywebview 6 will not center it"
    )
    assert main_w.kwargs.get("screen") is startup.webview.screens[0], (
        "the main window no longer names its screen; pywebview 6 will not "
        "center it the day maximized goes"
    )

    assert splash.kwargs.get("hidden") is True, (
        "the splash must start hidden; its own 'loaded' handler reveals it"
    )
    assert splash.kwargs.get("frameless") is True
    assert splash.kwargs.get("on_top") is True

    assert main_w.kwargs.get("hidden") is True, (
        "the main window must start hidden or the user meets the blank page"
    )
    assert main_w.kwargs.get("maximized") is True
    assert main_w.kwargs.get("confirm_close") is True
    assert main_w.kwargs.get("js_api") is not None, (
        "the main window lost its js_api bridge"
    )
    assert main_w.kwargs["js_api"]._window is main_w, (
        "js_api._window is not wired to the main window; both Browse "
        "buttons will silently do nothing in the real window"
    )

    assert set(splash.kwargs) == {
        "html", "width", "height", "frameless", "on_top",
        "background_color", "text_select", "hidden", "screen",
    }, "the splash's create_window keyword surface changed"
    assert set(main_w.kwargs) == {
        "hidden", "confirm_close", "width", "height", "min_size",
        "maximized", "js_api", "text_select", "screen",
    }, "the main window's create_window keyword surface changed"



def test_installed_pywebview_still_centers_screen_windows_explicitly():
    spec = importlib.util.find_spec("webview")
    assert spec is not None and spec.origin, "pywebview is not installed"
    winforms = Path(spec.origin).parent / "platforms" / "winforms.py"
    source = winforms.read_text(encoding="utf-8")

    version = importlib.metadata.version("pywebview")
    hint = (
        f"pywebview {version}'s winforms.py no longer matches: its "
        "screen-positioning code has moved. Re-verify the splash in the "
        "real window, then update this canary."
    )
    assert re.search(r"elif window\.screen\s*:", source), hint
    assert re.search(
        r"window\.screen\.x\s*\+\s*\(\s*window\.screen\.width"
        r"\s*-\s*window\.initial_width\s*\)\s*//\s*2",
        source,
    ), hint



def test_the_api_token_is_pushed_into_the_window_and_never_rendered(startup):
    seen = {}

    def scenario(splash, main_w):
        splash.events.loaded.fire()
        seen["backend_navigated"] = main_w.navigated.wait(T)
        seen["delivered"] = fire_until(
            main_w.events.loaded, lambda: bool(main_w.evaluated_js)
        )

    startup.run(scenario)

    _splash, main_w = startup.windows
    assert seen["backend_navigated"] is True
    assert seen["delivered"] is True, "the token was never pushed into the window"

    pushes = [js for js in main_w.evaluated_js if "__receiveApiToken" in js]
    assert pushes, f"no token push among {main_w.evaluated_js}"
    assert FAKE_SESSION_TOKEN in pushes[0]
    assert f'"{FAKE_SESSION_TOKEN}"' in pushes[0], (
        "the token must be JSON-quoted into the script, or a token containing "
        "a quote would break out of the expression"
    )


def test_the_token_is_not_pushed_at_the_blank_page(startup):
    startup.backend_gate = threading.Event()
    seen = {}

    def scenario(splash, main_w):
        main_w.events.loaded.fire()
        seen["pushed_at_blank_page"] = list(main_w.evaluated_js)
        splash.events.loaded.fire()
        startup.backend_gate.set()
        seen["backend_navigated"] = main_w.navigated.wait(T)
        seen["delivered_after"] = fire_until(
            main_w.events.loaded, lambda: bool(main_w.evaluated_js)
        )

    startup.run(scenario)

    assert seen["pushed_at_blank_page"] == [], (
        "the token must not be pushed before our page is the one loaded"
    )
    assert seen["backend_navigated"] is True
    assert seen["delivered_after"] is True


def test_the_token_is_redelivered_on_every_load(startup):
    seen = {}

    def scenario(splash, main_w):
        splash.events.loaded.fire()
        seen["backend_navigated"] = main_w.navigated.wait(T)
        fire_until(main_w.events.loaded, lambda: bool(main_w.evaluated_js))
        first = len(main_w.evaluated_js)
        main_w.events.loaded.fire()
        seen["after_refresh"] = len(main_w.evaluated_js)
        seen["first"] = first

    startup.run(scenario)

    assert seen["backend_navigated"] is True
    assert seen["first"] >= 1
    assert seen["after_refresh"] > seen["first"], (
        "a reload must re-deliver the token; the reloaded page is waiting for it"
    )


def test_a_failed_token_push_does_not_crash_the_startup(startup):
    seen = {}

    def scenario(splash, main_w):
        def explode(script):
            raise RuntimeError("WebView busy")

        main_w.evaluate_js = explode
        splash.events.loaded.fire()
        seen["backend_navigated"] = main_w.navigated.wait(T)
        seen["main_shown"] = fire_until(
            main_w.events.loaded, lambda: main_w.show_calls >= 1
        )

    startup.run(scenario)

    assert seen["backend_navigated"] is True
    assert seen["main_shown"] is True, "a failed push must not block the handoff"
    assert startup.panic_calls == [], "a failed push is not a fatal crash"


def test_browse_file_dialog_filters_to_txt(monkeypatch):
    fake_webview = blank_module("webview")
    fake_webview.FileDialog = types.SimpleNamespace(
        OPEN=object(), FOLDER=object(), SAVE=object()
    )
    monkeypatch.setitem(sys.modules, "webview", fake_webview)

    api = main_module.Api()
    recorded = {}

    class FakeWindow:
        def create_file_dialog(self, dialog_type, **kwargs):
            recorded["dialog_type"] = dialog_type
            recorded.update(kwargs)
            return (r"C:\anywhere\prompt.txt",)

    api._window = FakeWindow()

    assert api.browse_file() == r"C:\anywhere\prompt.txt"
    assert recorded["dialog_type"] is fake_webview.FileDialog.OPEN
    file_types = recorded.get("file_types")
    assert file_types, "the picker lost its .txt filter"
    assert file_types[0] == "Text files (*.txt)"
    assert any("(*.*)" in entry for entry in file_types[1:]), \
        "the All-files escape hatch is gone"
