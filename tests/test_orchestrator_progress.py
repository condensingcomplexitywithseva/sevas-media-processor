# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import logging
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from batch_orchestrator import BatchOrchestrator
from media_classifier import MediaClassifier
from schemas import Status, PageResult, FileSummary


class _StubDb:
    def get_highest_file_id(self):
        return 0

    def get_successfully_processed_relative_paths(self, statuses):
        return set()

    def handle_file_started(self, *args):
        pass

    def handle_frame_saved(self, *args):
        pass

    def handle_file_completed(self, *args):
        pass


class _StubLogger:
    def __init__(self):
        self.app_logger = logging.getLogger("test-orchestrator")

    def log_file_started(self, *args):
        pass

    def log_frame_saved(self, *args):
        pass

    def log_file_completed(self, *args):
        pass

    def log_critical_error(self, *args):
        pass


class _StubRouter:
    output_folder = Path(".")
    relative_or_orphan = staticmethod(MediaClassifier.relative_or_orphan)

    def evaluate_and_route(self, file_id, path, root):
        def gen():
            yield PageResult(1, "x.jpg", Status.OK.value, "")
            return FileSummary(1, "1", Status.OK.value, Status.OK.value, "done")

        rel, orphaned = self.relative_or_orphan(path, root)
        return rel, path.suffix, "Stub", gen(), orphaned


def test_progress_stays_below_100_inside_the_loop(tmp_path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(4):
        (input_dir / f"file_{i}.png").write_bytes(b"x")

    settings = SimpleNamespace(
        INPUT_FOLDER_PATH=input_dir,
        NO_RETRY_STATUSES=[Status.OK],
        JPEG_QUALITY=90,
        MAX_DIMENSION=4096,
        ENABLE_LLM_INFERENCE=False,
    )
    orchestrator = BatchOrchestrator(settings, _StubDb(), _StubRouter(), _StubLogger(), None)

    values = []
    orchestrator.execute_batch_processing_loop(
        on_progress=lambda msg: values.append(msg["value"]))

    assert values, "no progress events emitted"
    assert values == [0, 25, 50, 75]
    assert max(values) < 100


def test_abort_mid_batch_emits_aborted_event_and_keeps_partial_db(tmp_path, monkeypatch):
    import central_logger
    from app_context import ProcessorCore
    from config_validator import Settings

    monkeypatch.setattr(central_logger, "setup_logging", lambda *args, **kwargs: None)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(4):
        (input_dir / f"file_{i}.png").write_bytes(b"x")

    settings = Settings(
        INPUT_FOLDER_PATH=str(input_dir),
        OUTPUT_FOLDER_PATH=str(tmp_path / "output"),
        ENABLE_LLM_INFERENCE=False,
    )

    abort_flag = threading.Event()
    events = []

    def on_progress(event):
        events.append(event)
        if event == {"type": "progress", "value": 25}:
            abort_flag.set()

    ProcessorCore(settings, abort_flag, on_progress=on_progress).run()

    event_types = [event["type"] for event in events]
    assert event_types[-1] == "aborted"
    assert "done" not in event_types
    assert "failed" not in event_types
    progress_values = [e["value"] for e in events if e["type"] == "progress"]
    assert progress_values == [0, 25]

    db_path = settings.TECH_FOLDER_PATH / "application_state.db"
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT relative_file_path, final_aggregate_status"
            " FROM databasefileregistry ORDER BY unique_file_id"
        ).fetchall()
    finally:
        connection.close()

    assert [path for path, _ in rows] == ["file_0.png", "file_1.png"]
    assert all(status != "processing" for _, status in rows)
