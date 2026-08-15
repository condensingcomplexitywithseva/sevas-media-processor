# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from enum import Enum
from typing import NamedTuple
from dataclasses import dataclass

from fs_utils import humanize_paths


class ConfigurationError(Exception):
    pass


class Status(str, Enum):
    OK = "ok"
    FAILURE = "failure"
    PARTIAL_FAILURE = "partial failure"
    SKIPPED = "skipped"

    LLM_FAILED = "llm_failed"
    LLM_PARTIAL = "llm_partial"


class RangeStatus(str, Enum):
    OK = "ok"
    TRUNCATED = "truncated"
    SKIPPED = "skipped"
    PARTIAL_SKIP = "partial_skip"
    EMPTY_FILE = "empty_file"
    FAILURE = "failure"


class InferenceResult(NamedTuple):
    status: str
    answer: str
    error: str


@dataclass
class PageResult:
    page_number: int
    output_filename: str
    success: str
    comment: str
    capture_seconds: float | None = None

    def __post_init__(self):
        self.comment = humanize_paths(self.comment)


@dataclass
class FileSummary:
    total_discovered_pages: int
    applied_range_string: str
    range_status_code: str
    final_aggregate_status: str
    final_aggregate_comment: str

    def __post_init__(self):
        self.final_aggregate_comment = humanize_paths(self.final_aggregate_comment)
