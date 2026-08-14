# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import logging
import os
import sys
import threading
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

pytest.importorskip(
    "playwright.sync_api",
    reason="Playwright not installed (dev-only: pip install playwright && playwright install chromium)",
)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"Chromium unavailable for Playwright: {e}")
        yield b
        b.close()


@pytest.fixture
def app_server(tmp_path, monkeypatch):
    import central_logger
    import config_loader
    from config_loader import ConfigManager, TokenManager
    from werkzeug.serving import make_server

    servers = []

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
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}"

    yield start
    _restore_logging()
    for s in servers:
        s.shutdown()


@pytest.fixture
def open_page(browser, app_server):
    pages = []

    def open_(settings_overrides, raw_settings=None, tokens=None, deliver_token=True):
        url = app_server(settings_overrides, raw_settings=raw_settings, tokens=tokens)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url)

        if deliver_token:
            from routes.web_server import SESSION_TOKEN

            def deliver_api_token(*_):
                try:
                    page.evaluate(
                        "token => window.__receiveApiToken && window.__receiveApiToken(token)",
                        SESSION_TOKEN,
                    )
                except Exception:
                    pass

            page.on("load", deliver_api_token)
            deliver_api_token()

        page.wait_for_selector("body.lang-loaded", timeout=15000)
        page.evaluate("changeLanguage('en')")
        page.wait_for_timeout(200)
        pages.append(page)
        return page

    yield open_
    for p in pages:
        p.close()
