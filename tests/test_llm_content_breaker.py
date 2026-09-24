# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from batch_orchestrator import BatchOrchestrator
from media_classifier import MediaClassifier
from schemas import RequestOutcome, ConfigurationError, FileSummary, PageResult, Status

CONTENT_FAIL = [RequestOutcome(1, (1,), "invalid_json_answer", "not json", "")]
NETWORK_FAIL = [RequestOutcome(1, (1,), "network_failure", "", "boom")]
CLEAN = [RequestOutcome(1, (1,), "ok", "a fine answer", "")]
MIXED_FAIL = [
    RequestOutcome(1, tuple(range(1, 6)), "invalid_json_answer", "not json", ""),
    RequestOutcome(2, (6, 7, 8, 9), "network_failure", "", "boom"),
]
PARTLY_CLEAN = [
    RequestOutcome(1, tuple(range(1, 6)), "ok", "a fine answer", ""),
    RequestOutcome(2, (6, 7, 8, 9), "invalid_json_answer", "not json", ""),
]


def make_settings(input_dir, **overrides) -> SimpleNamespace:
    values: dict[str, Any] = {
        "INPUT_FOLDER_PATH": input_dir,
        "NO_RETRY_STATUSES": [Status.OK],
        "JPEG_QUALITY": 90,
        "MAX_DIMENSION": 4096,
        "ENABLE_LLM_INFERENCE": True,
        "MAX_CONSECUTIVE_LLM_FAILURES": 3,
        "MAX_JPEGS_PER_INFERENCE": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class RecordingDb:
    def __init__(self):
        self.llm_requests = []

    def finalize_file(self, file_id, expected_requests=0):
        pass

    def record_file_interruption(self, file_id, reason):
        self.interruption = (file_id, reason)

    def get_highest_file_id(self):
        return 0

    def get_no_retry_sources(self, results):
        return {}

    def handle_file_started(self, file_id, rel_path, ext, pipeline_name, **kwargs):
        pass

    def handle_frame_saved(self, file_id, page_result):
        pass

    def handle_file_completed(self, file_id, summary):
        pass

    def handle_llm_requests(self, file_id, outcomes):
        if self.llm_requests and self.llm_requests[-1][0] == file_id:
            self.llm_requests[-1][1].extend(outcomes)
        else:
            self.llm_requests.append((file_id, list(outcomes)))

    def get_file_statuses(self, file_id):
        return {}

    def get_successful_frames(self, file_id, output_folder):
        return [(1, Path("frame.jpg"), "")]


class StubLogger:
    def __init__(self):
        self.app_logger = logging.getLogger("test-llm-content-breaker")

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

    def evaluate_and_route(self, file_id, path, root):
        rel, orphaned = self.relative_or_orphan(path, root)

        def gen():
            yield PageResult(1, f"{path.stem}.jpg", Status.OK.value, "")
            return FileSummary(1, "1", Status.OK.value, "done")

        return rel, path.suffix, "Stub", gen(), orphaned


class ScriptedLLM:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def execute_network_inference(self, frames, abort_flag=None, *, on_outcome=None):
        assert on_outcome is not None
        self.calls += 1
        outcomes = self.results.pop(0)
        for outcome in outcomes:
            on_outcome(outcome)
        return outcomes


def run_files(tmp_path, scripted, threshold=3):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for index in range(len(scripted)):
        (input_dir / f"file_{index:02d}.png").write_bytes(b"x")

    llm = ScriptedLLM(scripted)
    db = RecordingDb()
    settings = make_settings(input_dir,
                             MAX_CONSECUTIVE_LLM_FAILURES=threshold)
    error = None
    try:
        BatchOrchestrator(
            settings, db, StubRouter(), StubLogger(), llm
        ).execute_batch_processing_loop()
    except ConfigurationError as caught:
        error = caught
    return llm, db, error



def test_content_breaker_trips_after_consecutive_invalid_json_files(tmp_path):
    llm, db, error = run_files(
        tmp_path, [CONTENT_FAIL, CONTENT_FAIL, CONTENT_FAIL, CLEAN],
        threshold=3)

    assert error is not None, "three all-invalid-JSON files must halt the run"
    assert "CIRCUIT BREAKER" in str(error)
    assert "valid JSON" in str(error)
    assert "no successful reply in between" not in str(error)
    assert llm.calls == 3
    assert [file_id for file_id, _ in db.llm_requests] == [1, 2, 3]


def test_a_clean_request_resets_the_content_counter(tmp_path):
    llm, _, error = run_files(
        tmp_path,
        [CONTENT_FAIL, CONTENT_FAIL, PARTLY_CLEAN, CONTENT_FAIL, CONTENT_FAIL],
        threshold=3)

    assert error is None
    assert llm.calls == 5


def test_network_failures_neither_feed_nor_reset_the_content_counter(tmp_path):
    llm, _, error = run_files(
        tmp_path, [CONTENT_FAIL, NETWORK_FAIL, CONTENT_FAIL], threshold=2)

    assert error is not None
    assert "valid JSON" in str(error)
    assert llm.calls == 3


def test_content_failures_do_not_feed_the_network_breaker_count(tmp_path):
    llm, _, error = run_files(
        tmp_path, [CONTENT_FAIL, CONTENT_FAIL, NETWORK_FAIL, NETWORK_FAIL],
        threshold=3)

    assert error is None
    assert llm.calls == 4


def test_a_mixed_failure_file_counts_as_a_network_failure(tmp_path):
    llm, _, error = run_files(
        tmp_path, [MIXED_FAIL, MIXED_FAIL, MIXED_FAIL], threshold=3)

    assert error is not None
    assert "no successful reply in between" in str(error)
    assert "valid JSON" not in str(error)
    assert llm.calls == 3


@pytest.mark.parametrize("neutral_status", [
    "aborted_by_user", "token_limit_exceeded", "encoding_failure",
    "not_attempted",
])
def test_neutral_outcomes_hold_the_content_counter(tmp_path, neutral_status):
    neutral = [RequestOutcome(1, (1,), neutral_status, "", "detail")]
    llm, _, error = run_files(
        tmp_path, [CONTENT_FAIL, CONTENT_FAIL, neutral, CONTENT_FAIL, CLEAN],
        threshold=3)

    assert error is not None
    assert "valid JSON" in str(error)
    assert llm.calls == 4


@pytest.mark.parametrize("scripted,wording", [
    pytest.param([CONTENT_FAIL] * 4 + [CLEAN], "valid JSON", id="content"),
    pytest.param([NETWORK_FAIL] * 4 + [CLEAN],
                 "no successful reply in between", id="network"),
])
def test_one_knob_governs_both_breakers(tmp_path, scripted, wording):
    llm, _, error = run_files(tmp_path, scripted, threshold=4)

    assert error is not None
    assert wording in str(error)
    assert llm.calls == 4
