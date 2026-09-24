# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from range_parsers import PageRangeSelector, VideoSelector
from schemas import ConfigurationError, FileSummary, RangeStatus, Status
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


@pytest.mark.parametrize("fragment_count", [10, 11, 25, 1001])
def test_page_range_format_preserves_every_fragment(fragment_count):
    indices = list(range(0, fragment_count * 2, 2))
    expected = ", ".join(str(index + 1) for index in indices)
    formatted = PageRangeSelector.format_range_string(list(reversed(indices)))
    assert formatted == expected
    assert PageRangeSelector(formatted).calculate_indices(indices[-1] + 1).indices == indices


@pytest.mark.parametrize("fragment_count", [10, 11, 25, 1001])
def test_time_range_format_preserves_every_fragment(fragment_count):
    times = [float(start + offset) for start in range(0, fragment_count * 10, 10)
             for offset in (0, 1)]
    expected = ", ".join(
        f"{start // 3600:02}:{start // 60 % 60:02}:{start % 60:02}-"
        f"{(start + 1) // 3600:02}:{(start + 1) // 60 % 60:02}:{(start + 1) % 60:02}"
        for start in range(0, fragment_count * 10, 10)
    )
    assert VideoSelector.format_time_range(list(reversed(times))) == expected



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
        yield from ()
        return FileSummary(0, "", Status.OK.value, "")


def _finalize(expected, ok, skipped, failed):
    dummy = _Dummy(SimpleNamespace(), 1, Path("x"), "x", ".x", Path("out"))
    return dummy.finalize_results(expected, ok, skipped, failed,
                                  10, "1-10", RangeStatus.OK.value, [])



def test_aggregate_all_ok_comment():
    assert _finalize(5, 5, 0, 0).file_to_jpegs_comment == \
        "Successfully saved all 5 requested frames"


def test_aggregate_ok_with_skips_comment():
    assert _finalize(5, 3, 2, 0).file_to_jpegs_comment == \
        "Processed 5 candidates: 3 saved, 2 skipped (static/duplicate)"


def test_aggregate_partial_failure_comment():
    assert _finalize(5, 3, 0, 2).file_to_jpegs_comment == \
        "Target 5 candidates: 3 saved, 0 skipped, 2 failed"


def test_aggregate_total_failure_comment():
    assert _finalize(5, 0, 0, 5).file_to_jpegs_comment == \
        "All 5 candidates failed to process"


def test_aggregate_zero_expected_reads_as_a_skip():
    assert _finalize(0, 0, 0, 0).file_to_jpegs_comment == \
        "Configured range does not overlap this file; nothing was extracted"


def test_aggregate_comment_deduplicates_repeated_errors():
    dummy = _Dummy(SimpleNamespace(), 1, Path("x"), "x", ".x", Path("out"))
    summary = dummy.finalize_results(
        3, 0, 0, 3, 10, "1-3", RangeStatus.OK.value,
        ["Render error: boom", "Render error: boom", "Render error: boom"],
    )
    assert summary.file_to_jpegs_comment.count("Render error: boom") == 1
