# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import logging
import os
import queue
import sys
from logging.handlers import MemoryHandler, RotatingFileHandler
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import central_logger
from central_logger import SSEBroadcaster, categorize


def test_categorize_table():
    assert categorize("config_loader") == "CONFIG"
    assert categorize("Settings") == "CONFIG"
    assert categorize("LLMClient") == "AI"
    assert categorize("llm_client") == "AI"
    assert categorize("Orchestrator.Worker") == "CORE"
    assert categorize("UI") == "UI"
    assert categorize("werkzeug") == "CORE"
    assert categorize("root") == "SYSTEM"
    assert categorize("SYSTEM") == "SYSTEM"
    assert categorize("SevasMediaProcessor") == "SevasMediaProcessor"


def test_emit_keeps_history_only_for_log_events():
    b = SSEBroadcaster()
    listener = queue.Queue()
    b.add_listener(listener)

    log_event = {"type": "log", "content": "hello"}
    progress_event = {"type": "progress", "value": 42}
    b.emit(log_event)
    b.emit(progress_event)

    assert listener.get_nowait() == log_event
    assert listener.get_nowait() == progress_event
    assert list(b.history) == [log_event], \
        "progress events must never enter the replay history"


def test_add_listener_replays_history():
    b = SSEBroadcaster()
    b.emit({"type": "log", "content": "early line"})
    late = queue.Queue()
    b.add_listener(late)
    assert late.get_nowait() == {"type": "log", "content": "early line"}



def emit_one(monkeypatch, message):
    fresh = SSEBroadcaster()
    monkeypatch.setattr(central_logger, "global_broadcaster", fresh)
    listener = queue.Queue()
    fresh.add_listener(listener)

    central_logger.GlobalSSEHandler().emit(
        logging.LogRecord("SevasMediaProcessor", logging.ERROR, __file__, 1,
                          message, None, None)
    )
    return listener.get_nowait()


def test_console_stream_strips_the_long_path_prefix_from_a_loose_log_line(monkeypatch):
    event = emit_one(
        monkeypatch,
        "Catastrophic error opening animation a.gif: cannot identify image "
        "file '\\\\\\\\?\\\\C:\\\\pics\\\\a.gif'",
    )
    assert "\\\\?\\" not in event["content"]
    assert "'C:\\pics\\a.gif'" in event["content"]


def test_console_stream_leaves_ordinary_lines_alone(monkeypatch):
    ordinary = "[3] STARTING: holidays/beach.jpg (Type: .jpg)"
    assert emit_one(monkeypatch, ordinary)["content"] == ordinary


@pytest.fixture
def clean_logging_state(monkeypatch, tmp_path):
    monkeypatch.setattr(central_logger, "get_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(central_logger, "_configured", False)
    monkeypatch.setattr(central_logger, "_active_file_handler", None)

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level

    yield tmp_path

    central_logger.close_logging()
    central_logger.global_broadcaster.history.clear()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in saved_handlers:
        root.addHandler(h)
    root.setLevel(saved_level)


def test_setup_logging_end_state(clean_logging_state):
    tmp_path = clean_logging_state
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    for i in range(35):
        stale = logs_dir / f"system_log_stale_{i:02d}.txt"
        stale.write_text("old", encoding="utf-8")
        os.utime(stale, (1000 + i, 1000 + i))

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    mem = MemoryHandler(capacity=100, target=None)
    root.addHandler(mem)
    root.setLevel(logging.DEBUG)
    logging.getLogger("boot").info("pre-boot probe line")

    central_logger.global_broadcaster.history.clear()
    central_logger.setup_logging("INFO", memory_buffer_handler=mem)

    kinds = sorted(type(h).__name__ for h in root.handlers)
    assert kinds == ["GlobalSSEHandler", "RotatingFileHandler", "StreamHandler"]
    assert root.level == logging.INFO

    assert mem not in root.handlers
    assert mem.buffer == []
    log_file = central_logger.get_active_log_file()
    assert log_file is not None and log_file.parent == logs_dir.resolve()
    assert "pre-boot probe line" in log_file.read_text(encoding="utf-8")

    assert any(
        "pre-boot probe line" in e.get("content", "")
        for e in central_logger.global_broadcaster.history
    )

    assert len(list(logs_dir.glob("system_log_*.txt*"))) == 31

    central_logger.setup_logging("DEBUG")
    assert len(root.handlers) == 3
    assert root.level == logging.INFO


def test_werkzeug_access_lines_cannot_leak_the_session_token(clean_logging_state):
    tmp_path = clean_logging_state
    central_logger.setup_logging("DEBUG")

    werkzeug_logger = logging.getLogger("werkzeug")
    assert werkzeug_logger.level == logging.ERROR
    assert not werkzeug_logger.isEnabledFor(logging.INFO)

    secret = "fake-session-token-for-redaction-test-0000"
    werkzeug_logger.info(
        '127.0.0.1 - - [08/Aug/2026 00:01:31] "GET /api/process/stream?token=%s HTTP/1.1" 200 -',
        secret,
    )
    logging.shutdown()

    broadcast = " ".join(
        str(event.get("content", ""))
        for event in central_logger.global_broadcaster.history
    )
    assert secret not in broadcast, "the session token reached the console stream"

    on_disk = " ".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (tmp_path / "logs").glob("system_log_*.txt*")
    )
    assert secret not in on_disk, "the session token reached the log file"


def test_setup_logging_survives_unwritable_logs_dir(clean_logging_state, monkeypatch):
    tmp_path = clean_logging_state

    def exploding_handler(*args, **kwargs):
        raise OSError("locked")

    monkeypatch.setattr(central_logger, "RotatingFileHandler", exploding_handler)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    mem = MemoryHandler(capacity=100, target=None)
    root.addHandler(mem)

    central_logger.setup_logging("INFO", memory_buffer_handler=mem)

    kinds = sorted(type(h).__name__ for h in root.handlers)
    assert kinds == ["GlobalSSEHandler", "StreamHandler"]
    assert central_logger.get_active_log_file() is None
    assert mem not in root.handlers
