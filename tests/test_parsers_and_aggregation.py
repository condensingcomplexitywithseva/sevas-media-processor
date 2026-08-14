# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from range_parsers import PageRangeSelector, VideoSelector
from schemas import ConfigurationError, RangeStatus, Status
from pipelines.base_pipeline import BaseMediaPipeline



def test_page_range_basic():
    result = PageRangeSelector("1-3, 5").calculate_indices(10)
    assert result.indices == [0, 1, 2, 4]
    assert result.status == RangeStatus.OK.value


def test_page_range_blank_means_everything():
    result = PageRangeSelector("").calculate_indices(4)
    assert result.indices == [0, 1, 2, 3]


def test_page_range_truncated_to_file_length():
    result = PageRangeSelector("1-100").calculate_indices(5)
    assert result.indices == [0, 1, 2, 3, 4]
    assert result.status == RangeStatus.TRUNCATED.value


def test_page_range_fully_out_of_bounds_is_skipped():
    result = PageRangeSelector("50-60").calculate_indices(5)
    assert result.indices == []
    assert result.status == RangeStatus.SKIPPED.value


def test_page_range_partial_skip():
    result = PageRangeSelector("1-2, 50-60").calculate_indices(5)
    assert result.indices == [0, 1]
    assert result.status == RangeStatus.PARTIAL_SKIP.value


def test_page_range_open_end():
    result = PageRangeSelector("3-").calculate_indices(6)
    assert result.indices == [2, 3, 4, 5]


def test_page_range_bad_syntax_raises():
    with pytest.raises(ConfigurationError):
        PageRangeSelector("abc")
    with pytest.raises(ConfigurationError):
        PageRangeSelector("5-2")


def test_format_range_string_roundtrip():
    assert PageRangeSelector.format_range_string([0, 1, 2, 4]) == "1-3, 5"



def test_video_selector_strict_format():
    with pytest.raises(ConfigurationError):
        VideoSelector("90")
    with pytest.raises(ConfigurationError):
        VideoSelector("00:99:00")
    with pytest.raises(ConfigurationError):
        VideoSelector("1e2:00:00")


def test_video_selector_point_and_range():
    sel = VideoSelector("00:00:05, 00:01:00-00:02:00")
    assert sel.segments[0].start_sec == 5.0 and sel.segments[0].is_point
    assert sel.segments[1].start_sec == 60.0 and sel.segments[1].end_sec == 120.0


def test_video_summary_mode_targets():
    sel = VideoSelector("")
    result = sel.get_target_times(100.0, "SUMMARY",
                                  {"TARGET_TOTAL_FRAMES": 5, "SCENE_SENSITIVITY": 0})
    assert len(result.times) == 5
    assert result.status == RangeStatus.OK.value
    assert result.times[0] == 0.0
    assert result.times[-1] == 100.0

    capped = sel.get_target_times(100.0, "SUMMARY",
                                  {"TARGET_TOTAL_FRAMES": 5, "SCENE_SENSITIVITY": 0},
                                  content_end_sec=99.4)
    assert len(capped.times) == 5
    assert capped.times[-1] == 99.4
    assert capped.status == RangeStatus.OK.value


def test_video_out_of_bounds_skipped():
    sel = VideoSelector("00:10:00-00:11:00")
    result = sel.get_target_times(60.0, "SUMMARY",
                                  {"TARGET_TOTAL_FRAMES": 5, "SCENE_SENSITIVITY": 0})
    assert result.times == []
    assert result.status == RangeStatus.SKIPPED.value



class _Dummy(BaseMediaPipeline):
    def process(self):
        yield


def _finalize(expected, ok, skipped, failed):
    dummy = _Dummy(SimpleNamespace(), 1, Path("x"), "x", ".x", Path("out"))
    return dummy.finalize_results(expected, ok, skipped, failed,
                                  10, "1-10", RangeStatus.OK.value, [])


def test_aggregate_all_ok():
    assert _finalize(5, 5, 0, 0).final_aggregate_status == Status.OK.value


def test_aggregate_ok_with_skips():
    assert _finalize(5, 3, 2, 0).final_aggregate_status == Status.OK.value


def test_aggregate_partial_failure():
    assert _finalize(5, 3, 0, 2).final_aggregate_status == Status.PARTIAL_FAILURE.value


def test_aggregate_total_failure():
    assert _finalize(5, 0, 0, 5).final_aggregate_status == Status.FAILURE.value


def test_aggregate_zero_expected_is_skip_not_failure():
    assert _finalize(0, 0, 0, 0).final_aggregate_status == Status.SKIPPED.value


def test_aggregate_comment_deduplicates_repeated_errors():
    dummy = _Dummy(SimpleNamespace(), 1, Path("x"), "x", ".x", Path("out"))
    summary = dummy.finalize_results(
        3, 0, 0, 3, 10, "1-3", RangeStatus.OK.value,
        ["Render error: boom", "Render error: boom", "Render error: boom"],
    )
    assert summary.final_aggregate_comment.count("Render error: boom") == 1
