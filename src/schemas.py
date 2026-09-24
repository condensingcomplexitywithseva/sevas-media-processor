# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from enum import Enum
from typing import NamedTuple
from dataclasses import dataclass

from fs_utils import humanize_paths


class ConfigurationError(Exception):
    request_outcomes: "list[RequestOutcome] | None" = None
    halted_request_answer: str = ""
    halted_request_pages: tuple[int, ...] = ()

    def __init__(self, message: str = "", *, setting_field: str = "", path: str = "",
                 detail_key: str = ""):
        super().__init__(message)
        self.setting_field = setting_field
        self.path = path
        self.detail_key = detail_key


MESSAGE_REFERENCES = {
    "err_export_no_results": {"output": "OUTPUT_FOLDER_PATH"},
    "err_export_no_folder": {"output": "OUTPUT_FOLDER_PATH"},
    "err_export_logs_no_folder": {"output": "OUTPUT_FOLDER_PATH"},
    "err_archive_locked": {"output": "OUTPUT_FOLDER_PATH"},
    "err_quality_floor_above_start": {"floor": "LOWEST_QUALITY", "start": "JPEG_QUALITY"},
    "err_resume_configuration_changed": {
        "columns": "LLM_OUTPUT_COLUMNS", "format": "LLM_OUTPUT_MODE", "mode": "ENABLE_LLM_INFERENCE",
    },
    "hint_llm_output_mode": {"images": "MAX_JPEGS_PER_INFERENCE"},
    "hint_local_context": {"images": "MAX_JPEGS_PER_INFERENCE"},
    "hint_lowest_qual": {"size": "MAX_FILE_SIZE_KB"},
    "hint_max_size": {"quality": "LOWEST_QUALITY", "resolution": "MAX_DIMENSION"},
}


def message_envelope(payload: dict) -> dict:
    result = dict(payload)
    raw = str(result.get("message", result.get("error", "")))
    key = result.get("message_key", "")
    detail = result.get("detail", "")
    if raw.startswith("i18n:"):
        key, _, detail = raw[5:].partition("|")
        raw = ""
    if detail == result.get("path"):
        detail = ""
    result.update(status=result.get("status", "error"), message_key=key,
                  message=raw, detail=detail, field=result.get("field"),
                  path=result.get("path", ""), args=result.get("args", {}),
                  refs={**MESSAGE_REFERENCES.get(key, {}), **result.get("refs", {})})
    return result


def exception_message(error: Exception) -> dict:
    payload = {"status": "error", "message": str(error),
               "field": getattr(error, "setting_field", "") or None,
               "path": getattr(error, "path", "")}
    detail_key = getattr(error, "detail_key", "")
    if detail_key:
        payload["detail_key"] = detail_key
    return message_envelope(payload)


class RunConfiguration(NamedTuple):
    ai_enabled: bool
    output_mode: str
    declared_columns: tuple[str, ...]


class SourceFingerprint(NamedTuple):
    size: int
    mtime_ns: int
    sha256: str


class Status(str, Enum):
    OK = "ok"
    FAILURE = "failure"
    SKIPPED = "skipped"


class RequestStatus(str, Enum):
    OK = "ok"
    ABORTED_BY_USER = "aborted_by_user"
    ENCODING_FAILURE = "encoding_failure"
    TOKEN_LIMIT_EXCEEDED = "token_limit_exceeded"
    NETWORK_FAILURE = "network_failure"
    PROVIDER_REPLY_PARSE_ERROR = "provider_reply_parse_error"
    INVALID_JSON_ANSWER = "invalid_json_answer"
    NOT_ATTEMPTED = "not_attempted"
    INTERRUPTED = "interrupted"


class OverallResult(str, Enum):
    OK = "ok"
    PARTIAL_FAIL = "partial_fail"
    FAIL = "fail"
    SKIPPED = "skipped"


class RangeStatus(str, Enum):
    OK = "ok"
    TRUNCATED = "truncated"
    SKIPPED = "skipped"
    PARTIAL_SKIP = "partial_skip"
    FAILURE = "failure"


class AnswerRow(NamedTuple):
    page_number: int | None
    raw_model_page_number: str
    llm_error: str
    values_json: str


class RequestOutcome(NamedTuple):
    request_number: int
    pages: tuple[int, ...]
    status: str
    raw_answer: str
    error: str
    answer_rows: tuple[AnswerRow, ...] = ()


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
    total_pages: int
    page_range: str
    range_status: str
    file_to_jpegs_comment: str

    def __post_init__(self):
        self.file_to_jpegs_comment = humanize_paths(self.file_to_jpegs_comment)
