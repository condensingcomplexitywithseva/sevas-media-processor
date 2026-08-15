# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import logging
import math
import numpy as np
from typing import NamedTuple
from pydantic import BaseModel, ValidationError, model_validator

from schemas import RangeStatus, ConfigurationError

logger = logging.getLogger(__name__)


class SummaryIndicesResult(NamedTuple):
    indices: list[int]
    request_met: bool
    available_frames: int

class SummaryTimesResult(NamedTuple):
    times: list[float]
    request_met: bool
    segment_duration: float

class SamplingTimesResult(NamedTuple):
    times: list[float]
    hit_budget_limit: bool
    theoretical_count: int

class PageRangeResult(NamedTuple):
    indices: list[int]
    status: str
    details: str

class TimeRangeResult(NamedTuple):
    times: list[float]
    status: str
    details: str


def truncate_visual_ranges(formatted_parts: list[str], limit: int = 10) -> str:
    if len(formatted_parts) > limit:
        half = int(limit / 2)
        head = formatted_parts[:half]
        tail = formatted_parts[-half:]
        omitted = len(formatted_parts) - limit
        return ", ".join(head) + f", ... ({omitted} ranges omitted) ..., " + ", ".join(tail)
    return ", ".join(formatted_parts)

def calculate_summary_indices(start_idx: int, end_idx: int, target_count: int) -> SummaryIndicesResult:
    if target_count <= 0 or start_idx >= end_idx:
        return SummaryIndicesResult([], False, 0)

    available_frames = end_idx - start_idx

    if available_frames <= target_count:
        logger.debug(f"Summary Math: Available frames ({available_frames}) is <= target ({target_count}). Taking all.")
        return SummaryIndicesResult(list(range(start_idx, end_idx)), False, available_frames)

    raw_indices = np.linspace(start_idx, end_idx - 1, target_count, dtype=int)

    final_indices = sorted(np.unique(raw_indices).tolist())

    return SummaryIndicesResult(final_indices, True, available_frames)

def calculate_summary_times(start_sec: float, end_sec: float, target_count: int) -> SummaryTimesResult:
    if target_count <= 0 or start_sec >= end_sec:
        return SummaryTimesResult([], False, 0.0)

    segment_duration = end_sec - start_sec

    raw_times = np.linspace(start_sec, end_sec, target_count, endpoint=True)
    final_times = sorted(np.unique(raw_times).tolist())

    request_met = len(final_times) == target_count

    if not request_met:
        logger.debug(f"Summary Math: Floating point collision reduced {target_count} targets to {len(final_times)}.")

    return SummaryTimesResult(final_times, request_met, segment_duration)

def calculate_sampling_times(start_sec: float, end_sec: float, step_seconds: float,
                             max_budget: int) -> SamplingTimesResult:
    if max_budget <= 0 or step_seconds <= 0 or start_sec >= end_sec:
        return SamplingTimesResult([], False, 0)

    duration = end_sec - start_sec
    theoretical_count = math.floor(duration / step_seconds) + 1

    raw_times = np.arange(start_sec, end_sec + 1e-9, step_seconds)

    hit_budget = False

    if len(raw_times) > max_budget:
        hit_budget = True
        logger.debug(f"Sampling Math: Theoretical frames ({len(raw_times)}) exceeds budget ({max_budget}). Truncating.")
        raw_times = [start_sec] if max_budget == 1 else raw_times[:max_budget]

    return SamplingTimesResult(sorted(float(t) for t in raw_times),
                               hit_budget, theoretical_count)


class PageSegmentModel(BaseModel):
    start: int
    end: int | None = None

    @model_validator(mode="after")
    def validate_bounds(self) -> "PageSegmentModel":
        if self.start < 0:
            raise ValueError("Start page cannot be negative.")
        if self.end is not None and self.start >= self.end:
            raise ValueError("Start page cannot be greater than end page.")
        return self

class PageRangeSelector:
    class Segment(NamedTuple):
        start: int
        end: int | None

    def __init__(self, range_string: str):
        self.raw_string = range_string
        self.segments: list[PageRangeSelector.Segment] = []
        self._compile()

    def _compile(self):
        if not self.raw_string:
            self.segments.append(self.Segment(0, None))
            return

        parts = self.raw_string.split(",")
        for part in parts:
            part = part.strip()
            if not part:
                continue

            try:
                if part.startswith("-"):
                    start_page = 0
                    end_string = part[1:]
                elif part.endswith("-"):
                    start_page = int(part[:-1]) - 1
                    end_string = None
                elif "-" in part:
                    start_string, end_string = part.split("-")
                    start_page = int(start_string) - 1
                else:
                    start_page = int(part) - 1
                    end_string = part

                valid_segment = PageSegmentModel(start=start_page, end=int(end_string) if end_string else None)
                self.segments.append(self.Segment(valid_segment.start, valid_segment.end))

            except (ValueError, ValidationError) as parsing_error:
                logger.error(f"Page parser syntax error on part: '{part}'. Reason: {parsing_error}")
                raise ConfigurationError(
                    f"Invalid page range syntax: '{part}'. Details: {parsing_error!s}"
                ) from parsing_error

    def calculate_indices(self, total_pages: int) -> PageRangeResult:
        requested_indices = set()
        has_out_of_bounds = False
        has_truncation = False

        for segment in self.segments:
            if segment.start >= total_pages:
                has_out_of_bounds = True
                continue

            current_end = total_pages if segment.end is None else min(segment.end, total_pages)

            if segment.end is not None and segment.end > total_pages:
                has_truncation = True

            for index in range(segment.start, current_end):
                if 0 <= index < total_pages:
                    requested_indices.add(index)

        final_indices = sorted(requested_indices)

        if not final_indices:
            logger.debug(f"Range check: All requested segments were outside file length ({total_pages}).")
            return PageRangeResult([], RangeStatus.SKIPPED.value, f"Out of bounds (Len: {total_pages})")

        warnings = []
        status_code = RangeStatus.OK.value

        if has_out_of_bounds:
            logger.debug("Range check: Partial skip triggered (some starts > file length).")
            warnings.append("Ranges exceeded length")
            status_code = RangeStatus.PARTIAL_SKIP.value

        if has_truncation:
            logger.debug("Range check: Truncation triggered (end clamped to file length).")
            warnings.append("Truncated to limit")
            if status_code == RangeStatus.OK.value:
                status_code = RangeStatus.TRUNCATED.value

        return PageRangeResult(final_indices, status_code, "; ".join(warnings))

    @staticmethod
    def format_range_string(indices: list[int], truncate: bool = True) -> str:
        if not indices:
            return ""

        pages = sorted([index + 1 for index in indices])
        formatted_parts = []
        range_start = pages[0]
        range_end = pages[0]

        for current_page in pages[1:]:
            if current_page == range_end + 1:
                range_end = current_page
            else:
                if range_start == range_end:
                    formatted_parts.append(str(range_start))
                else:
                    formatted_parts.append(f"{range_start}-{range_end}")
                range_start = current_page
                range_end = current_page

        if range_start == range_end:
            formatted_parts.append(str(range_start))
        else:
            formatted_parts.append(f"{range_start}-{range_end}")

        if truncate:
            return truncate_visual_ranges(formatted_parts)
        return ", ".join(formatted_parts)


class TimeSegmentModel(BaseModel):
    start_sec: float
    end_sec: float | None = None
    is_point: bool

    @model_validator(mode="after")
    def validate_bounds(self) -> "TimeSegmentModel":
        if self.end_sec is not None and self.start_sec > self.end_sec:
            raise ValueError("Start time exceeds end time")
        return self

class VideoSelector:

    class Segment(NamedTuple):
        start_sec: float
        end_sec: float | None
        is_point: bool

    def __init__(self, range_string: str):
        self.raw_string = range_string.strip()
        self.segments: list[VideoSelector.Segment] = []
        self._compile()

    def _to_sec(self, time_string: str) -> float:
        time_string = time_string.strip()
        if not time_string:
            raise ValueError("Empty time string")

        try:
            parts = time_string.split(":")
            if len(parts) != 3:
                raise ValueError("Format must be HH:MM:SS (e.g. 00:00:00)")

            h_str, m_str, s_str = (p.strip() for p in parts)

            import re as _re
            if not _re.fullmatch(r"\d+", h_str) or not _re.fullmatch(r"\d+", m_str) \
                    or not _re.fullmatch(r"\d+(\.\d+)?", s_str):
                raise ValueError("Format must be HH:MM:SS (e.g. 00:00:00)")

            h = float(h_str)
            m = float(m_str)
            s = float(s_str)

            if m < 0 or m >= 60:
                raise ValueError("Minutes must be between 00 and 59")
            if s < 0 or s >= 60:
                raise ValueError("Seconds must be between 00 and 59")
            if h < 0:
                raise ValueError("Hours cannot be negative")

            return h * 3600 + m * 60 + s
        except ValueError as ve:
            if "Format must be" in str(ve) or "must be between" in str(ve) or "cannot be negative" in str(ve):
                raise ve
            raise ValueError(f"Invalid time values in '{time_string}'. Use HH:MM:SS") from ve
        except Exception as error:
            raise ValueError(f"Invalid time format: '{time_string}'. Must be HH:MM:SS") from error

    def _compile(self):
        if not self.raw_string:
            self.segments.append(self.Segment(0.0, None, False))
            return

        for part in self.raw_string.split(","):
            part = part.strip()
            if not part:
                continue

            try:
                if "-" in part:
                    start_string, end_string = part.split("-")
                    start_time = self._to_sec(start_string) if start_string.strip() else 0.0
                    end_time = self._to_sec(end_string) if end_string.strip() else None

                    valid_segment = TimeSegmentModel(start_sec=start_time, end_sec=end_time, is_point=False)
                    self.segments.append(self.Segment(
                        valid_segment.start_sec, valid_segment.end_sec, valid_segment.is_point))
                else:
                    point_time = self._to_sec(part)
                    self.segments.append(self.Segment(point_time, point_time, True))

            except Exception as parsing_error:
                logger.error(f"Video parser syntax error on part: '{part}'. Reason: {parsing_error}")
                raise ConfigurationError(
                    f"Strict HH:MM:SS required for '{part}'. Example: 00:00:10. {parsing_error!s}"
                ) from parsing_error

    def get_target_times(
        self, duration: float, mode: str, config: dict, content_end_sec: float | None = None
    ) -> TimeRangeResult:
        if duration <= 0:
            return TimeRangeResult([], RangeStatus.SKIPPED.value, f"Invalid duration ({duration:.2f}s)")

        end_cap = duration
        if content_end_sec is not None and 0 < content_end_sec < duration:
            end_cap = content_end_sec

        requested_times = set()
        has_out_of_bounds = False
        has_truncation = False
        warnings = []

        for segment in self.segments:
            if segment.start_sec >= duration:
                has_out_of_bounds = True
                continue

            actual_end_time = segment.end_sec if segment.end_sec is not None else duration

            if actual_end_time > duration:
                has_truncation = True
                actual_end_time = duration

            generation_end = min(actual_end_time, end_cap)
            if generation_end <= segment.start_sec:
                generation_end = actual_end_time

            if segment.is_point:
                requested_times.add(segment.start_sec)
            else:
                if mode == "SUMMARY":
                    summary_result = calculate_summary_times(
                        segment.start_sec, generation_end, config["TARGET_TOTAL_FRAMES"]
                    )
                    requested_times.update(summary_result.times)

                    if not summary_result.request_met:
                        warnings.append(f"Dense Request: Extracted {len(summary_result.times)}")

                elif mode == "SAMPLING":
                    sampling_result = calculate_sampling_times(
                        segment.start_sec, generation_end, 1.0/config["CAPTURE_RATE_FPS"],
                        config["MAX_FRAMES_BUDGET"]
                    )
                    requested_times.update(sampling_result.times)

                    if sampling_result.hit_budget_limit:
                        has_truncation = True
                        warnings.append(f"Budget Limit: {config['MAX_FRAMES_BUDGET']}"
                                        f"/{sampling_result.theoretical_count}")

        final_times = sorted(requested_times)
        status_code = RangeStatus.OK.value

        if has_out_of_bounds:
            logger.debug("Video Range Check: Partial skip triggered (starts > duration).")
            warnings.append("OOB timestamps skipped")
            status_code = RangeStatus.PARTIAL_SKIP.value

        if has_truncation:
            logger.debug("Video Range Check: Truncation triggered (clamped to duration).")
            warnings.append("Truncated")
            if status_code == RangeStatus.OK.value:
                status_code = RangeStatus.TRUNCATED.value

        if not final_times:
            logger.debug(f"Video Range Check: All final timestamps stripped/invalid on {duration:.2f}s video.")
            status_code = RangeStatus.SKIPPED.value
            warnings.append("No targets in range")

        return TimeRangeResult(final_times, status_code, "; ".join(warnings))

    @staticmethod
    def format_time_range(times: list[float], truncate: bool = True) -> str:
        if not times:
            return ""

        def convert_seconds_to_hms(seconds: float) -> str:
            return f"{int(seconds//3600):02}:{int((seconds%3600)//60):02}:{int(seconds%60):02}"

        if len(times) == 1:
            return convert_seconds_to_hms(times[0])

        times = sorted(times)

        time_differences = [times[i] - times[i - 1] for i in range(1, len(times))]
        step_interval = min(time_differences) if time_differences else 0.0

        formatted_parts = []
        start_time = times[0]
        previous_time = times[0]

        for current_time in times[1:]:
            if math.isclose(current_time - previous_time, step_interval, rel_tol=1e-4, abs_tol=1e-4):
                previous_time = current_time
            else:
                if math.isclose(start_time, previous_time, abs_tol=1e-4):
                    formatted_parts.append(convert_seconds_to_hms(start_time))
                else:
                    formatted_parts.append(f"{convert_seconds_to_hms(start_time)}-{convert_seconds_to_hms(previous_time)}")

                start_time = current_time
                previous_time = current_time

        if math.isclose(start_time, previous_time, abs_tol=1e-4):
            formatted_parts.append(convert_seconds_to_hms(start_time))
        else:
            formatted_parts.append(f"{convert_seconds_to_hms(start_time)}-{convert_seconds_to_hms(previous_time)}")

        if truncate:
            return truncate_visual_ranges(formatted_parts)
        return ", ".join(formatted_parts)
