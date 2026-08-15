# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import logging
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.exc import SQLAlchemyError

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from batch_orchestrator import BatchOrchestrator
from db_controller import SQLiteDatabaseController
from media_classifier import MediaClassifier
from schemas import (
    ConfigurationError,
    FileSummary,
    InferenceResult,
    PageResult,
    Status,
)

LLM_FAIL = InferenceResult(status=Status.LLM_FAILED.value, answer="[TOTAL LLM NETWORK FAILURE]", error="boom")
LLM_OK = InferenceResult(status=Status.OK.value, answer="a fine answer", error="")


def make_settings(input_dir, **overrides) -> SimpleNamespace:
    values: dict[str, Any] = {
        "INPUT_FOLDER_PATH": input_dir,
        "NO_RETRY_STATUSES": [Status.OK],
        "JPEG_QUALITY": 90,
        "MAX_DIMENSION": 4096,
        "ENABLE_LLM_INFERENCE": False,
        "MAX_CONSECUTIVE_LLM_FAILURES": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class RecordingDb:

    def __init__(self, frame_paths=("frame.jpg",)):
        self.frame_paths = [Path(p) for p in frame_paths]
        self.started = []
        self.frames = []
        self.completed = []
        self.llm_completed = []

    def get_highest_file_id(self):
        return 0

    def get_successfully_processed_relative_paths(self, statuses):
        return set()

    def handle_file_started(self, file_id, rel_path, ext, pipeline_name):
        self.started.append((file_id, rel_path))

    def handle_frame_saved(self, file_id, page_result):
        self.frames.append((file_id, page_result))

    def handle_file_completed(self, file_id, summary):
        self.completed.append((file_id, summary))

    def handle_llm_completed(self, file_id, answer, error, status):
        self.llm_completed.append((file_id, answer, error, status))

    def get_successful_frame_paths(self, file_id, output_folder):
        return list(self.frame_paths)


class StubLogger:
    def __init__(self):
        self.app_logger = logging.getLogger("test-orchestrator-resilience")

    def log_file_started(self, *args):
        pass

    def log_frame_saved(self, *args):
        pass

    def log_file_completed(self, *args):
        pass

    def log_llm_completed(self, *args):
        pass

    def log_critical_error(self, *args):
        pass


class StubRouter:

    output_folder = Path(".")
    relative_or_orphan = staticmethod(MediaClassifier.relative_or_orphan)

    def __init__(self):
        self.routed = []

    def evaluate_and_route(self, file_id, path, root):
        self.routed.append(path.name)
        rel, orphaned = self.relative_or_orphan(path, root)
        return rel, path.suffix, "Stub", self.make_generator(path), orphaned

    def make_generator(self, path):
        def gen():
            yield PageResult(1, f"{path.stem}.jpg", Status.OK.value, "")
            return FileSummary(1, "1", Status.OK.value, Status.OK.value, "done")

        return gen()


class CountingLLM:

    def __init__(self, results=()):
        self.results = list(results)
        self.calls = 0

    def execute_network_inference(self, image_paths, abort_flag=None):
        self.calls += 1
        return self.results.pop(0)


class ForbiddenLLM:

    def __init__(self):
        self.calls = 0

    def execute_network_inference(self, image_paths, abort_flag=None):
        self.calls += 1
        return LLM_OK


def make_input(tmp_path, *names):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for name in names:
        (input_dir / name).write_bytes(b"x")
    return input_dir



def test_resume_skips_completed_files_before_any_pipeline_is_built(tmp_path):
    input_dir = make_input(tmp_path, "done.png", "new.png")

    db = SQLiteDatabaseController(tmp_path / "state.db")
    db.handle_file_started(7, "done.png", ".png", "Stub")
    db.handle_file_completed(7, FileSummary(1, "1", "ok", Status.OK.value, "from a previous run"))

    router = StubRouter()
    orchestrator = BatchOrchestrator(
        make_settings(input_dir), db, router, StubLogger(), None
    )
    orchestrator.execute_batch_processing_loop()
    db.close()

    assert router.routed == ["new.png"]

    connection = sqlite3.connect(tmp_path / "state.db")
    try:
        rows = connection.execute(
            "SELECT unique_file_id, relative_file_path FROM databasefileregistry"
            " ORDER BY unique_file_id"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [(7, "done.png"), (8, "new.png")]



def test_empty_input_folder_ends_gracefully_at_100(tmp_path, caplog):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    db = RecordingDb()
    events = []

    with caplog.at_level(logging.WARNING):
        BatchOrchestrator(
            make_settings(input_dir), db, StubRouter(), StubLogger(), None
        ).execute_batch_processing_loop(on_progress=lambda e: events.append(e))

    assert events == [{"type": "progress", "value": 100}]
    assert db.started == [] and db.completed == []
    assert "empty" in caplog.text.lower()



def test_circuit_breaker_trips_after_consecutive_llm_failures(tmp_path):
    input_dir = make_input(tmp_path, "a.png", "b.png", "c.png", "d.png")
    llm = CountingLLM([LLM_FAIL, LLM_FAIL, LLM_FAIL, LLM_OK])
    settings = make_settings(
        input_dir, ENABLE_LLM_INFERENCE=True, MAX_CONSECUTIVE_LLM_FAILURES=3
    )

    with pytest.raises(ConfigurationError, match="CIRCUIT BREAKER"):
        BatchOrchestrator(
            settings, RecordingDb(), StubRouter(), StubLogger(), llm
        ).execute_batch_processing_loop()

    assert llm.calls == 3


def test_one_llm_success_resets_the_failure_counter(tmp_path):
    input_dir = make_input(tmp_path, "a.png", "b.png", "c.png", "d.png", "e.png")
    llm = CountingLLM([LLM_FAIL, LLM_FAIL, LLM_OK, LLM_FAIL, LLM_FAIL])
    settings = make_settings(
        input_dir, ENABLE_LLM_INFERENCE=True, MAX_CONSECUTIVE_LLM_FAILURES=3
    )

    BatchOrchestrator(
        settings, RecordingDb(), StubRouter(), StubLogger(), llm
    ).execute_batch_processing_loop()

    assert llm.calls == 5



def test_file_with_no_valid_frames_gets_llm_failure_without_network_call(tmp_path):
    input_dir = make_input(tmp_path, "broken.png")
    db = RecordingDb(frame_paths=())
    settings = make_settings(input_dir, ENABLE_LLM_INFERENCE=True)
    llm = ForbiddenLLM()

    BatchOrchestrator(
        settings, db, StubRouter(), StubLogger(), llm
    ).execute_batch_processing_loop()

    assert llm.calls == 0
    assert db.llm_completed == [
        (1, "No images provided for network inference.", "No valid frames extracted.", Status.FAILURE.value)
    ]



def test_crash_after_completion_never_overwrites_the_record(tmp_path):
    input_dir = make_input(tmp_path, "a.png", "b.png")

    class ExplodingPrepDb(RecordingDb):
        def get_successful_frame_paths(self, file_id, output_folder):
            raise RuntimeError("AI prep exploded")

    db = ExplodingPrepDb()
    settings = make_settings(input_dir, ENABLE_LLM_INFERENCE=True)

    BatchOrchestrator(
        settings, db, StubRouter(), StubLogger(), ForbiddenLLM()
    ).execute_batch_processing_loop()


    assert [file_id for file_id, _ in db.completed] == [1, 2]
    assert all(summary.final_aggregate_comment == "done" for _, summary in db.completed)


def test_crash_before_completion_writes_a_fatal_crash_summary(tmp_path):
    input_dir = make_input(tmp_path, "torn.png")

    class TornRouter(StubRouter):
        def make_generator(self, path):
            def gen():
                yield PageResult(1, "torn.jpg", Status.OK.value, "")
                raise ValueError("decoder blew up mid-file")

            return gen()

    db = RecordingDb()
    BatchOrchestrator(
        make_settings(input_dir), db, TornRouter(), StubLogger(), None
    ).execute_batch_processing_loop()

    assert len(db.completed) == 1
    _, summary = db.completed[0]
    assert summary.final_aggregate_status == Status.FAILURE.value
    assert summary.final_aggregate_comment.startswith("Fatal Orchestration Exception")
    assert "decoder blew up" in summary.final_aggregate_comment


def test_database_errors_escalate_to_runtime_error(tmp_path):
    input_dir = make_input(tmp_path, "a.png")

    class BrokenDb(RecordingDb):
        def handle_file_started(self, *args, **kwargs):
            raise SQLAlchemyError("disk gone")

    with pytest.raises(RuntimeError, match="Fatal DB Transaction Error"):
        BatchOrchestrator(
            make_settings(input_dir), BrokenDb(), StubRouter(), StubLogger(), None
        ).execute_batch_processing_loop()



def test_stop_during_extraction_prevents_the_llm_call(tmp_path):
    input_dir = make_input(tmp_path, "a.png")
    abort_flag = threading.Event()

    class AbortingRouter(StubRouter):
        def make_generator(self, path):
            def gen():
                yield PageResult(1, "a.jpg", Status.OK.value, "")
                abort_flag.set()
                yield PageResult(2, "b.jpg", Status.OK.value, "")
                return FileSummary(2, "1-2", Status.OK.value, Status.OK.value, "done")

            return gen()

    db = RecordingDb()
    settings = make_settings(input_dir, ENABLE_LLM_INFERENCE=True)
    llm = ForbiddenLLM()

    BatchOrchestrator(
        settings, db, AbortingRouter(), StubLogger(), llm
    ).execute_batch_processing_loop(abort_flag=abort_flag)

    assert llm.calls == 0
    assert len(db.completed) == 1
    assert "Aborted mid-extraction" in db.completed[0][1].final_aggregate_comment
    assert db.llm_completed == []
