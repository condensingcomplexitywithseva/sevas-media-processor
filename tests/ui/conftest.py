# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import contextlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
TESTS = Path(__file__).resolve().parents[1]
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from working_folder import fresh_working_folder  # noqa: F401  (registers the autouse fixture)

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright not installed (dev-only: pip install playwright && playwright install chromium)",
)


@pytest.fixture(scope="session")
def _playwright():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        yield p


class _BrowserKeeper:

    def __init__(self, playwright):
        self.playwright = playwright
        self.browser = None
        self.relaunches = 0

    def live(self):
        if self.browser is not None and not self.browser.is_connected():
            self.relaunch("Chromium died during the session")
        if self.browser is None:
            try:
                self.browser = self.playwright.chromium.launch()
            except Exception as e:
                pytest.skip(f"Chromium unavailable for Playwright: {e}")
        return self.browser

    def relaunch(self, reason):
        if self.browser is not None:
            with contextlib.suppress(Exception):
                self.browser.close()
            self.relaunches += 1
            logging.getLogger("tests.ui").warning(
                "%s; relaunching (relaunch %d).", reason, self.relaunches)
        self.browser = None

    def close(self):
        if self.browser is not None and self.browser.is_connected():
            self.browser.close()


VIEWPORT = {"width": 1400, "height": 900}
_CRASH_SIGNS = ("Target crashed", "has been closed")


def new_page_with_one_retry(keeper):
    try:
        return keeper.live().new_page(viewport=VIEWPORT)
    except Exception as error:
        if not any(sign in str(error) for sign in _CRASH_SIGNS):
            raise
        keeper.relaunch(f"page open failed ({error})")
        return keeper.live().new_page(viewport=VIEWPORT)


UI_BROWSER_SLOTS = 6

UI_TEST_TIMEOUT_SECONDS = 180


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    ui_dir = Path(__file__).resolve().parent
    by_file: dict[Path, list] = {}
    for item in items:
        path = Path(str(item.path)).resolve()
        if path.parent == ui_dir:
            by_file.setdefault(path, []).append(item)
    loads = [0] * UI_BROWSER_SLOTS
    for path in sorted(by_file, key=lambda p: (-len(by_file[p]), p.name)):
        slot = loads.index(min(loads))
        loads[slot] += len(by_file[path])
        for item in by_file[path]:
            item.add_marker(pytest.mark.xdist_group(f"ui-{slot}"))
            if item.get_closest_marker("timeout") is None:
                item.add_marker(pytest.mark.timeout(UI_TEST_TIMEOUT_SECONDS))


@pytest.fixture(scope="session")
def _browser_keeper(_playwright):
    keeper = _BrowserKeeper(_playwright)
    yield keeper
    keeper.close()


class _ServerDrain:

    def __init__(self, server, broadcaster):
        self.server = server
        self.broadcaster = broadcaster
        self.closing = threading.Event()
        self.condition = threading.Condition()
        self.active = 0
        self.closed = False
        dispatch = server.process_request
        process = server.process_request_thread
        application = server.app

        def dispatch_tracked(*args):
            with self.condition:
                self.active += 1
            try:
                dispatch(*args)
            except BaseException:
                self.finished()
                raise

        def process_tracked(*args):
            try:
                process(*args)
            finally:
                self.finished()

        def stop_stream(environ, start_response):
            response = application(environ, start_response)
            try:
                for chunk in response:
                    if environ.get('PATH_INFO') == '/api/process/stream' and self.closing.is_set():
                        break
                    yield chunk
            finally:
                if hasattr(response, 'close'):
                    response.close()

        server.process_request = dispatch_tracked
        server.process_request_thread = process_tracked
        server.app = stop_stream
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    def finished(self):
        with self.condition:
            self.active -= 1
            self.condition.notify_all()

    def wait_for_requests(self):
        deadline = time.monotonic() + 10
        with self.condition:
            while self.active:
                self.broadcaster.emit({'type': 'test_shutdown'})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f'{self.active} UI fixture requests did not finish')
                self.condition.wait(min(0.05, remaining))

    def close(self):
        if self.closed:
            return
        self.server.shutdown()
        self.closing.set()
        try:
            self.wait_for_requests()
        finally:
            self.server.server_close()
            self.thread.join(timeout=2)
        self.closed = True


@pytest.fixture
def server_drains():
    return []


@pytest.fixture
def app_server(tmp_path, monkeypatch, server_drains):
    import central_logger
    import config_loader
    from config_loader import ConfigManager, TokenManager
    from werkzeug.serving import make_server

    from routes import execution_api
    monkeypatch.setattr(execution_api, "run_controller", execution_api.RunController())

    servers = server_drains

    monkeypatch.setattr(central_logger, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(central_logger, "_configured", False)

    root = logging.getLogger()
    prev_root_level = root.level
    saved_handlers = list(root.handlers)
    root.setLevel(logging.INFO)
    central_logger.global_broadcaster.history.clear()
    root.addHandler(central_logger.global_sse_handler)
    prev_werkzeug_level = logging.getLogger("werkzeug").level
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    def _restore_logging():
        central_logger.close_logging()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(prev_root_level)
        logging.getLogger("werkzeug").setLevel(prev_werkzeug_level)
        central_logger.global_broadcaster.history.clear()

    for key in list(os.environ):
        if key.endswith("_TOKEN"):
            monkeypatch.delenv(key)

    import routes.export_api as export_api
    monkeypatch.setattr(export_api, "_reveal_in_explorer", lambda path: False)
    monkeypatch.setattr(export_api, "_open_in_explorer", lambda folder: False)

    def start(settings_overrides, raw_settings=None, tokens=None):
        mgr = ConfigManager(tmp_path)
        mgr.token_manager = TokenManager(tmp_path / ".env")
        mgr.app_data_dir = tmp_path
        mgr.env_path = tmp_path / ".env"
        (tmp_path / "input").mkdir(exist_ok=True)

        if tokens:
            mgr.token_manager.update_tokens(tokens)

        if raw_settings is not None:
            mgr.settings_path.write_text(raw_settings, encoding="utf-8")
        else:
            payload = {
                "INPUT_FOLDER_PATH": str(tmp_path / "input"),
                "OUTPUT_FOLDER_PATH": str(tmp_path / "output"),
            }
            payload.update(settings_overrides)
            mgr.settings_path.write_text(json.dumps(payload), encoding="utf-8")

        monkeypatch.setattr(config_loader, "_manager", mgr)

        from routes.web_server import create_app

        server = make_server("127.0.0.1", 0, create_app(), threaded=True)
        servers.append(_ServerDrain(server, central_logger.global_broadcaster))
        return f"http://127.0.0.1:{server.server_port}"

    yield start
    try:
        for server in servers:
            server.close()
    finally:
        _restore_logging()


@pytest.fixture
def open_page(_browser_keeper, app_server):
    pages = []

    def open_(settings_overrides, raw_settings=None, tokens=None, deliver_token=True):
        url = app_server(settings_overrides, raw_settings=raw_settings, tokens=tokens)
        page = new_page_with_one_retry(_browser_keeper)
        page.goto(url)

        if deliver_token:
            from routes.web_server import SESSION_TOKEN

            def deliver_api_token(*_):
                with contextlib.suppress(Exception):
                    page.evaluate(
                        "token => window.__receiveApiToken && window.__receiveApiToken(token)",
                        SESSION_TOKEN,
                    )

            page.on("load", deliver_api_token)
            deliver_api_token()

        page.wait_for_selector("body.lang-loaded", timeout=15000)
        page.evaluate("changeLanguage('en')")
        page.wait_for_timeout(200)
        pages.append(page)
        return page

    yield open_
    for p in pages:
        with contextlib.suppress(Exception):
            p.close()
