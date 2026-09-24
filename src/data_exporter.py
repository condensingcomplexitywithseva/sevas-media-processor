# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import csv
import errno
import json
import logging
import math
import os
import re
import sqlite3
import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from fs_utils import get_safe_path, replace_lone_surrogates
from schemas import ConfigurationError, OverallResult, RequestStatus, RunConfiguration, Status

exporter_logger = logging.getLogger("DataExporter")
EXCEL_MAX_ROWS = 1_000_000
EXCEL_MAX_COLUMNS = 16_000
EXCEL_MAX_CELL_CHARS = 32_000
TRUNCATION_MARKER = "[TEXT SHORTENED] "
ROW_OMISSION_MARKER = "[ROWS OMITTED]"
COLUMN_OMISSION_MARKER = "[COLUMNS OMITTED]"
WORKBOOK_COMPLETE_LINE = "No rows, columns or cell text shortened"
EXPORT_WRITE_ATTEMPTS = 4
REGISTRY_REPORT_COLUMNS = [
    "file_id",
    "file_path",
    "file_ext",
    "total_pages",
    "page_range",
    "range_status",
    "file_to_jpegs_comment",
    "file_to_jpegs_status",
    "file_to_llm_status",
    "overall_result",
]
PAGE_LOG_REPORT_COLUMNS = [
    "file_id",
    "page_id",
    "page_number",
    "output_file",
    "page_to_jpeg_status",
    "page_to_jpeg_comment",
    "video_frame_timestamp",
]
LLM_REQUESTS_REPORT_COLUMNS = [
    "file_id",
    "request_id",
    "request_number",
    "pages",
    "request_status",
    "raw_llm_answer",
    "llm_network_error",
]
LLM_ANSWERS_REPORT_COLUMNS = [
    "file_id",
    "page_id",
    "request_id",
    "llm_answer_id",
    "raw_model_page_number",
    "llm_error",
]
DB_ONLY_FIELDS = {
    "llm_answers": ("values_json",),
    "file_registry": ("processing_complete", "ai_enabled", "processing_error",
                      "source_size", "source_mtime_ns", "source_sha256"),
    "run_configuration": ("configuration_id", "ai_enabled", "output_mode", "declared_columns",
                          "source_root", "processing_digest"),
}
_REGISTRY_QUERY = """
SELECT f.*, j.file_to_jpegs_status, l.file_to_llm_status, o.overall_result,
       COUNT(*) OVER (PARTITION BY f.file_path) AS attempt_count,
       ROW_NUMBER() OVER (PARTITION BY f.file_path ORDER BY f.file_id) AS attempt_number,
       {source_neighbours}
FROM file_registry f JOIN file_to_jpegs_status j USING(file_id)
JOIN file_to_llm_status l USING(file_id) JOIN overall_result o USING(file_id)
ORDER BY f.file_id
"""
_SOURCE_NEIGHBOURS = (
    "f.source_sha256 AS current_source, "
    "LAG(f.source_sha256) OVER (PARTITION BY f.file_path ORDER BY f.file_id) AS previous_source, "
    "LEAD(f.source_sha256) OVER (PARTITION BY f.file_path ORDER BY f.file_id) AS next_source")
_NO_SOURCE_NEIGHBOURS = "'' AS current_source, '' AS previous_source, '' AS next_source"


def _registry_query(con: sqlite3.Connection) -> str:
    columns = {row[1] for row in con.execute("PRAGMA table_info(file_registry)")}
    return _REGISTRY_QUERY.format(source_neighbours=_SOURCE_NEIGHBOURS if "source_sha256" in columns
                                  else _NO_SOURCE_NEIGHBOURS)


_XML_FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_REQUEST_NOTES = {
    "aborted_by_user": "Request stopped by the user",
    "encoding_failure": "Images could not be prepared; request not sent",
    "token_limit_exceeded": "Reply exceeded the response limit; raw reply in LLM Requests",
    "network_failure": "Request failed",
    "provider_reply_parse_error": "Provider reply unreadable; raw reply in LLM Requests",
    "invalid_json_answer": "Answer format invalid; raw reply in LLM Requests",
    "not_attempted": "Request not sent",
    "interrupted": "Request interrupted; provider execution may have occurred",
}
_ANSWER_NOTES = {
    "hallucinated_page_number": "Unverified page claim",
    "duplicate_page_number": "Additional answer for the same page",
    "no_row_returned": "No answer returned for this page",
}


@dataclass
class Report:
    title: str
    stem: str
    headers: list[str]
    rows: Callable[[], Iterator[list[Any]]]


@dataclass
class ExportOutcome:
    database: str
    directory: str
    saved: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    recovery: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "error" if self.failed else "success",
            "database": self.database,
            "path": self.directory,
            "saved": self.saved,
            "failed": self.failed,
            "notices": self.notices,
            "recovery": self.recovery,
        }


class ExportError(RuntimeError):
    def __init__(self, outcome: ExportOutcome):
        self.outcome = outcome
        super().__init__("; ".join(f"{r['report']}: {r['error']}" for r in outcome.failed))


def error_category(exc: Exception) -> str:
    winerror = getattr(exc, "winerror", None)
    code = getattr(exc, "errno", None)
    if winerror in (32, 33) or code in (errno.EBUSY, errno.EAGAIN):
        return "locked"
    if winerror in (39, 112) or code == errno.ENOSPC:
        return "disk_full"
    if isinstance(exc, ImportError):
        return "installation"
    if isinstance(exc, PermissionError):
        return "access_denied"
    if isinstance(exc, FileNotFoundError):
        return "unavailable"
    if isinstance(exc, (ValueError, ConfigurationError, sqlite3.DatabaseError)):
        return "data"
    return "write_failed"


def _cell(value: Any) -> Any:
    return replace_lone_surrogates(value) if isinstance(value, str) else value


def _utf16_prefix(value: str, units: int) -> str:
    return value.encode("utf-16-le")[: max(0, units) * 2].decode("utf-16-le", errors="ignore")


def _excel_string(value: str) -> str:
    return re.sub(r"_(?=x[0-9a-fA-F]{4}_)", "_x005F_", value).replace("\r", "_x000D_")


def _header_line_widths(value: str) -> list[float]:
    def units(char: str) -> float:
        if char in "ilI.,'`!|:;":
            return 0.6
        if not char.isascii():
            return 2.4
        if char in "MWmw":
            return 2.0
        return 1.5 if char.isupper() else 1.2

    return [sum(units(char) for char in line)
            for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


class SQLiteDataExporter:
    def __init__(self, target_database_path: Path):
        self.database_path = target_database_path

    @staticmethod
    def cell_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return replace_lone_surrogates(value)
        return json.dumps(value, ensure_ascii=False, allow_nan=False)

    @staticmethod
    def _values(raw: str, columns: Sequence[str], error: str) -> dict[str, Any]:
        if not raw and error == "no_row_returned":
            return {}

        def reject(value: str) -> Any:
            raise ValueError(f"Non-finite stored value: {value}")

        values = json.loads(raw, parse_constant=reject)
        if not isinstance(values, dict) or set(values) != set(columns):
            raise ValueError("Stored answer keys do not match the run's declared columns")
        json.dumps(values, allow_nan=False)
        return values

    def _spread_cells(self, values_json: str, columns: list[str], error: str) -> list[str]:
        values = self._values(values_json, columns, error)
        return [self.cell_text(values.get(key)) for key in columns]

    def _configuration(self, con: sqlite3.Connection) -> RunConfiguration:
        try:
            rows = list(con.execute("SELECT ai_enabled,output_mode,declared_columns FROM run_configuration"))
            if len(rows) != 1:
                raise ValueError("Missing or ambiguous run configuration")
            enabled, mode, raw = rows[0]
            keys = json.loads(raw)
            if enabled not in (0, 1) or not isinstance(keys, list):
                raise ValueError("Invalid run configuration")
            if any(not isinstance(key, str) or not key for key in keys) or len(set(keys)) != len(keys):
                raise ValueError("Invalid declared columns")
            if (enabled and (mode not in ("table_per_file", "table_per_page") or not keys)) or (
                not enabled and (mode or keys)
            ):
                raise ValueError("Invalid active configuration")
            return RunConfiguration(bool(enabled), mode, tuple(keys))
        except (ValueError, TypeError, sqlite3.DatabaseError) as exc:
            raise ConfigurationError(f"Cannot export {self.database_path}: {exc}") from exc

    def _registry_rows(self, con: sqlite3.Connection) -> Iterator[list[Any]]:
        for row in con.execute(_registry_query(con)):
            yield [row[h] for h in REGISTRY_REPORT_COLUMNS]

    @staticmethod
    def _validate_references(con: sqlite3.Connection) -> None:
        broken = con.execute("""SELECT a.llm_answer_id FROM llm_answers a
            LEFT JOIN llm_requests r ON r.request_id=a.request_id
            LEFT JOIN file_registry f ON f.file_id=a.file_id
            LEFT JOIN page_log p ON p.page_id=a.page_id
            WHERE r.request_id IS NULL OR f.file_id IS NULL OR r.file_id != a.file_id
               OR (a.page_id IS NOT NULL AND (p.page_id IS NULL OR p.file_id != a.file_id))
            LIMIT 1""").fetchone()
        if broken:
            raise ValueError(f"Stored answer {broken[0]} has inconsistent record references")
        orphan = con.execute("""SELECT r.request_id FROM llm_requests r
            LEFT JOIN file_registry f ON f.file_id=r.file_id WHERE f.file_id IS NULL LIMIT 1""").fetchone()
        if orphan:
            raise ValueError(f"Stored request {orphan[0]} has no file record")
        orphan_page = con.execute("""SELECT p.page_id FROM page_log p
            LEFT JOIN file_registry f ON f.file_id=p.file_id WHERE f.file_id IS NULL LIMIT 1""").fetchone()
        if orphan_page:
            raise ValueError(f"Stored page {orphan_page[0]} has no file record")

    def _results_rows(self, con: sqlite3.Connection, config: RunConfiguration) -> Iterator[list[Any]]:
        columns = list(config.declared_columns)
        self._validate_references(con)
        if not config.ai_enabled and con.execute("SELECT 1 FROM llm_requests LIMIT 1").fetchone():
            raise ValueError("AI-off run contains AI request records")
        has_completion = "processing_complete" in {
            row[1] for row in con.execute("PRAGMA table_info(file_registry)")}
        for f in con.execute(_registry_query(con)):
            file_id = f["file_id"]
            notes: list[str] = []
            if has_completion and not f["processing_complete"]:
                notes.append("Incomplete attempt; diagnostic only; not reused")
                if f["processing_error"]:
                    notes.append(f["processing_error"])
            if f["attempt_count"] > 1:
                notes.append(f"File attempt {f['attempt_number']}")
            current = f["current_source"]
            if current and f["previous_source"] and f["previous_source"] != current:
                notes.append(f"Source file changed since attempt {f['attempt_number'] - 1}")
            if current and f["next_source"] and f["next_source"] != current:
                notes.append("Source file changed after this attempt")
            page_facts = con.execute(
                """SELECT COUNT(*),
                SUM(page_to_jpeg_status='ok') FROM page_log WHERE file_id=?
                AND page_to_jpeg_status NOT IN ('skipped')""",
                (file_id,),
            ).fetchone()
            failed_count = con.execute(
                """SELECT COUNT(*) FROM page_log WHERE file_id=?
                AND page_to_jpeg_status NOT IN ('ok','skipped')""",
                (file_id,),
            ).fetchone()[0]
            if failed_count:
                notes.append(f"{failed_count} page(s) failed conversion; details in Page Log")
            comment = f["file_to_jpegs_comment"]
            if (
                comment
                and not comment.startswith(("Successfully saved all ", "Saved ", "Assigned to:"))
                and comment != "done"
            ):
                notes.append(comment)
            if f["range_status"] == "truncated":
                notes.append("Fewer pages or frames than requested: the range ends past the source "
                             "or a page or frame limit applied; details in Master Registry")
            elif f["range_status"] == "partial_skip":
                notes.append("Part of the requested range is outside the source; details in Master Registry")
            if not f["range_status"]:
                notes.append("File completion was not recorded")
            request_facts = con.execute(
                "SELECT COUNT(*), SUM(request_status != 'ok') FROM llm_requests WHERE file_id=?", (file_id,)
            ).fetchone()
            request_count, failed_requests = request_facts
            if not request_count:
                if config.ai_enabled and page_facts and page_facts[1]:
                    notes.append("AI enabled but no request outcome recorded; AI completion cannot be confirmed")
                row = [f["file_path"]]
                if config.ai_enabled:
                    row.append("")
                yield [file_id, *row, f["overall_result"], *([""] * len(columns)), " | ".join(notes)]
                continue
            if failed_requests and request_count > 1:
                notes.append("One or more requests failed or were not sent; details in LLM Requests")
            attempts = iter(con.execute(
                "SELECT request_number,COUNT(*) FROM llm_requests WHERE file_id=? "
                "GROUP BY request_number ORDER BY request_number", (file_id,)))
            last_number = None
            attempt_number = attempt_count = 0
            for r in con.execute(
                "SELECT * FROM llm_requests WHERE file_id=? ORDER BY request_number,request_id", (file_id,)
            ):
                if r["request_number"] != last_number:
                    last_number, attempt_count = next(attempts)
                    attempt_number = 0
                attempt_number += 1
                request_notes: list[str] = []
                if attempt_count > 1:
                    request_notes.append(f"Request {last_number}, attempt {attempt_number} of {attempt_count}")
                prefix = f"Request {r['request_number']}: " if request_count > 1 else ""
                if r["llm_network_error"]:
                    request_notes.append(prefix + r["llm_network_error"])
                if r["request_status"] != "ok":
                    request_notes.append(
                        prefix + _REQUEST_NOTES.get(str(r["request_status"]), str(r["request_status"]))
                    )
                request_notes.extend(notes)
                seen = False
                for a in con.execute(
                    """SELECT a.*, p.page_number FROM llm_answers a
                    LEFT JOIN page_log p USING(page_id) WHERE a.request_id=? ORDER BY a.llm_answer_id""",
                    (r["request_id"],),
                ):
                    seen = True
                    answer_notes = list(request_notes)
                    error = a["llm_error"]
                    if error:
                        note = _ANSWER_NOTES.get(str(error), str(error))
                        if error == "hallucinated_page_number":
                            note += ": " + a["raw_model_page_number"]
                        answer_notes.append(note)
                    cells = self._spread_cells(a["values_json"], columns, error)
                    if not error and all(value == "" for value in cells):
                        answer_notes.append("AI returned empty values")
                    page: Any = r["pages"] if config.output_mode == "table_per_file" and not error else a["page_number"]
                    if error == "hallucinated_page_number":
                        page = ""
                    yield [
                        file_id,
                        f["file_path"],
                        self._page_cell(page),
                        f["overall_result"],
                        *cells,
                        " | ".join(dict.fromkeys(answer_notes)),
                    ]
                if not seen:
                    if r["request_status"] == "ok":
                        request_notes.append("Request marked successful but no answer row recorded")
                    yield [
                        file_id,
                        f["file_path"],
                        self._page_cell(r["pages"]),
                        f["overall_result"],
                        *([""] * len(columns)),
                        " | ".join(dict.fromkeys(request_notes)),
                    ]

    @staticmethod
    def _page_cell(value: Any) -> Any:
        if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
            return int(value) if len(value) < 15 else value
        return "" if value is None else value

    def _reports(self, con: sqlite3.Connection, config: RunConfiguration) -> list[Report]:
        columns = list(config.declared_columns)

        def query(sql: str) -> Callable[[], Iterator[list[Any]]]:
            return lambda: (list(row) for row in con.execute(sql))

        headers = [
            "file_id",
            "file_path",
            *(["pages"] if config.ai_enabled else []),
            "file_result",
            *(f"llm_{key}" for key in columns),
            "notes",
        ]
        reports = [
            Report("Results", "results", headers, lambda: self._results_rows(con, config)),
            Report("Master Registry", "file_registry", REGISTRY_REPORT_COLUMNS, lambda: self._registry_rows(con)),
            Report(
                "Page Log",
                "page_log",
                PAGE_LOG_REPORT_COLUMNS,
                query(f"SELECT {','.join(PAGE_LOG_REPORT_COLUMNS)} FROM page_log ORDER BY page_id"),
            ),
        ]
        if config.ai_enabled:
            reports.append(
                Report(
                    "LLM Requests",
                    "llm_requests",
                    LLM_REQUESTS_REPORT_COLUMNS,
                    query(f"SELECT {','.join(LLM_REQUESTS_REPORT_COLUMNS)} FROM llm_requests ORDER BY request_id"),
                )
            )

            def answers() -> Iterator[list[Any]]:
                for row in con.execute("SELECT * FROM llm_answers ORDER BY llm_answer_id"):
                    yield [
                        *(row[h] for h in LLM_ANSWERS_REPORT_COLUMNS),
                        *self._spread_cells(row["values_json"], columns, row["llm_error"]),
                    ]

            reports.append(
                Report(
                    "LLM Answers",
                    "llm_answers",
                    [*LLM_ANSWERS_REPORT_COLUMNS, *(f"llm_{key}" for key in columns)],
                    answers,
                )
            )
        return reports

    @staticmethod
    def _summary(con: sqlite3.Connection, config: RunConfiguration) -> list[list[Any]]:
        rows: list[list[Any]] = []
        for section, table, column, values in (
            ("file records", "overall_result", "overall_result", [s.value for s in OverallResult]),
            ("pages", "page_log", "page_to_jpeg_status", [s.value for s in Status]),
            *(
                (("requests", "llm_requests", "request_status", [s.value for s in RequestStatus]),)
                if config.ai_enabled
                else ()
            ),
        ):
            counts = dict(con.execute(f"SELECT {column},COUNT(*) FROM {table} GROUP BY {column}"))
            rows.extend([section, key, counts.get(key, 0)] for key in values)
            if section == "file records":
                rows.append(
                    [
                        section,
                        "distinct files",
                        con.execute("SELECT COUNT(DISTINCT file_path) FROM file_registry").fetchone()[0],
                    ]
                )
        if config.ai_enabled:
            counts = dict(con.execute("SELECT llm_error,COUNT(*) FROM llm_answers GROUP BY llm_error"))
            rows.extend(["answers", key, counts[key]] for key in _ANSWER_NOTES if counts.get(key, 0))
        return rows

    @staticmethod
    def _unique_path(path: Path) -> Path:
        original = path
        number = 2
        while Path(get_safe_path(path)).exists():
            path = original.with_stem(f"{original.stem}-{number}")
            number += 1
        return path

    @staticmethod
    def _remove_temporary(temporary: Path | None) -> None:
        if temporary is None:
            return
        try:
            Path(get_safe_path(temporary)).unlink(missing_ok=True)
        except OSError as exc:
            exporter_logger.warning("Could not remove temporary report %s: %s", temporary.name, exc)

    @staticmethod
    def _release_reservation(fd: int) -> None:
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            try:
                os.close(fd)
            except OSError as exc:
                exporter_logger.warning("Could not close temporary report descriptor: %s", exc)
            raise
        try:
            handle.close()
        finally:
            if not handle.closed:
                try:
                    handle.close()
                except OSError as exc:
                    exporter_logger.warning("Could not close temporary report descriptor: %s", exc)

    def _atomic_write(self, path: Path, write: Callable[[Path], None]) -> Path:
        temporary: Path | None = None
        complete = False
        try:
            for attempt in range(EXPORT_WRITE_ATTEMPTS):
                try:
                    if not complete:
                        fd, name = tempfile.mkstemp(prefix=".report-", suffix=".tmp", dir=get_safe_path(path.parent))
                        temporary = Path(name)
                        self._release_reservation(fd)
                        write(temporary)
                        complete = True
                    assert temporary is not None
                    target = self._unique_path(path)
                    os.rename(get_safe_path(temporary), get_safe_path(target))
                    return target
                except OSError as exc:
                    if complete and isinstance(exc, FileExistsError):
                        continue
                    if error_category(exc) != "locked" or attempt + 1 >= EXPORT_WRITE_ATTEMPTS:
                        raise
                    if complete:
                        path = path.with_stem(path.stem + "-retry")
                    else:
                        self._remove_temporary(temporary)
                        temporary = None
                    time.sleep(0.25)
            raise FileExistsError("Could not allocate an export filename")
        finally:
            self._remove_temporary(temporary)

    def _write_csv(self, target_path: Path, headers: list[str], rows: Iterator[list[Any]]) -> None:
        with open(get_safe_path(target_path), "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
            writer.writerow([_cell(v) for v in headers])
            for row in rows:
                writer.writerow([_cell(v) for v in row])

    def _workbook(
        self,
        target: Path,
        reports: list[Report],
        summary_rows: list[list[Any]],
        outcome: ExportOutcome,
        *,
        recovery: bool = False,
    ) -> None:
        import openpyxl
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Alignment, Font
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet._write_only import WriteOnlyWorksheet
        from openpyxl.worksheet._writer import WorksheetWriter
        from openpyxl.writer.excel import ExcelWriter

        workbook = openpyxl.Workbook(write_only=True)
        summary = workbook.create_sheet("Summary")
        summary.column_dimensions["A"].width = 32
        summary.column_dimensions["B"].width = 85
        summary.freeze_panes = "A2"
        text_alignment = Alignment(wrap_text=True, vertical="top")
        header_font = Font(bold=True)
        summary_title_font = Font(bold=True, size=16)
        summary_section_font = Font(bold=True, size=14)
        error_font = Font(bold=True, color="FFFF0000")
        counts = {"cells": 0, "substitutions": 0}
        notices: list[str] = []
        summary_notices: list[tuple[str, str]] = []
        cut_fields: dict[tuple[str, int, str], int] = {}
        row_numbers: dict[str, int] = {}
        saved_csvs = {r["report"]: Path(r["path"]).name for r in outcome.saved if r["format"] == "csv"}

        def full_data(title: str) -> str:
            return saved_csvs.get(title, "CSV unavailable; retained database")

        def preview(value: str, limit: int = 120) -> str:
            value = value.replace("\r", " ").replace("\n", " ")
            return value if len(value) <= limit else value[:limit] + "..."

        def clean(
            value: Any, title: str, number: int, column: str, capped: bool, csv_record: int | None
        ) -> tuple[Any, bool]:
            if isinstance(value, int) and abs(value) > 999_999_999_999_999:
                value = str(value)
            if not isinstance(value, str):
                return value, False
            value, substitutions = _XML_FORBIDDEN.subn("\ufffd", value)
            counts["substitutions"] += substitutions
            cap = EXCEL_MAX_CELL_CHARS if capped else 32_000
            shortened = len(value.encode("utf-16-le")) // 2 > cap
            if shortened:
                marker = _XML_FORBIDDEN.sub("\ufffd", TRUNCATION_MARKER)
                if capped:
                    where = f"record {csv_record}" if csv_record is not None else "header"
                    reference = (
                        f"{marker}Full text: {full_data(title)}; {where}, column {number}. "
                        if title in saved_csvs
                        else f"{marker}CSV unavailable; full data remains in the run database. Retry export. "
                    )
                    if len(reference.encode("utf-16-le")) // 2 < cap:
                        marker = reference
                    counts["cells"] += 1
                    key = (title, number, column)
                    cut_fields[key] = cut_fields.get(key, 0) + 1
                marker = _utf16_prefix(marker, cap)
                value = marker + _utf16_prefix(value, cap - len(marker.encode("utf-16-le")) // 2)
            return value, shortened

        def append(
            sheet: Any,
            values: Sequence[Any],
            *,
            capped: bool = True,
            red: bool = False,
            header: bool = False,
            headers: Sequence[str] = (),
            csv_record: int | None = None,
            merged: bool = False,
        ) -> None:
            row_number = row_numbers.get(sheet.title, 0) + 1
            row_numbers[sheet.title] = row_number
            cells = []
            lines_needed = 1
            summary_heading = sheet.title == "Summary" and merged and header
            heading_size = 16 if row_number == 1 else 14
            for number, original in enumerate(values, 1):
                column = headers[number - 1] if number <= len(headers) else get_column_letter(number)
                value, _ = clean(original, sheet.title, number, column, capped, csv_record)
                cell = WriteOnlyCell(sheet, value=value)
                cell.alignment = (
                    Alignment(horizontal="left", wrap_text=True, vertical="top")
                    if sheet.title == "Summary" else text_alignment
                )
                if isinstance(value, str):
                    cell.data_type = "s"
                    setattr(cell, "_value", _excel_string(value))  # noqa: B010
                    width = 120 if merged else sheet.column_dimensions[get_column_letter(number)].width
                    if header and sheet.title != "Summary":
                        available = max(1, (width or 22) - 3)
                        line_count = sum(max(1, math.ceil(units / available)) for units in _header_line_widths(value))
                    else:
                        font_scale = heading_size / 11 if summary_heading else 1
                        available = max(1, int((width or 22) / font_scale) - (3 if header else 1))
                        line_count = sum(
                            max(1, (len(line) + available - 1) // available)
                            for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")
                        )
                    lines_needed = max(lines_needed, line_count)
                if red:
                    cell.font = error_font
                elif summary_heading:
                    cell.font = summary_title_font if row_number == 1 else summary_section_font
                elif header:
                    cell.font = header_font
                cells.append(cell)
            if lines_needed > 1 or summary_heading or (header and sheet.title != "Summary"):
                line_height = heading_size * 1.4 if summary_heading else 15
                sheet.row_dimensions[row_number].height = min(409, lines_needed * line_height + 4)
            sheet.append(cells)
            sheet.row_dimensions.pop(row_number, None)

        def introduction() -> list[str]:
            if recovery:
                return [
                    "Recovery report",
                    "Results could not be produced. These sheets contain the available raw records.",
                    "Retry export in the app. The failures are listed below.",
                ]
            files = {row[1]: row[2] for row in summary_rows if row[0] == "file records"}
            records = sum(files.get(status.value, 0) for status in OverallResult)
            distinct = files.get("distinct files", 0)
            ai_on = any(row[0] == "requests" for row in summary_rows)
            file_count = f"{distinct} file" + ("s" if distinct != 1 else "")
            coverage = file_count if records == distinct else f"{records} attempts for {file_count}"
            lines = [
                "Your processing report",
                coverage + (" - AI enabled" if ai_on else " - AI off"),
                "If you need more detail, you can use file_id to find the same file in Master Registry and Page Log"
                + (", and the AI detail sheets." if ai_on else "."),
            ]
            if records != distinct:
                lines.append("A file processed more than once counts as a separate attempt each time.")
            return lines

        def summary_line(line: str, *, bold: bool = False) -> None:
            number = row_numbers.get("Summary", 0) + 1
            summary.merged_cells.add(f"A{number}:B{number}")
            append(summary, [line], capped=False, header=bold, merged=True)

        def summary_heading(title: str, columns: Sequence[str]) -> None:
            append(summary, [], capped=False)
            summary_line(title, bold=True)
            append(summary, columns, capped=False, header=True)

        def count_sections() -> None:
            labels = {
                "file records": ("Files", {
                    "ok": "Fully processed", "partial_fail": "Partly processed",
                    "fail": "Failed", "skipped": "Skipped", "distinct files": "Distinct files",
                }),
                "pages": ("Pages", {"ok": "Converted", "failure": "Failed", "skipped": "Skipped"}),
                "requests": ("AI requests", {
                    "ok": "Completed", "aborted_by_user": "Stopped by user",
                    "encoding_failure": "Images could not be prepared",
                    "token_limit_exceeded": "Reply exceeded response limit",
                    "network_failure": "Request failed", "provider_reply_parse_error": "Unreadable provider reply",
                    "invalid_json_answer": "Invalid answer format", "not_attempted": "Not sent",
                    "interrupted": "Interrupted; execution uncertain",
                }),
                "answers": ("Answer problems", _ANSWER_NOTES),
            }
            files = {r[1]: r[2] for r in summary_rows if r[0] == "file records"}
            repeated = sum(files.get(s.value, 0) for s in OverallResult) != files.get("distinct files", 0)
            for section, (title, names) in labels.items():
                rows = []
                for group, item, count in summary_rows:
                    if group != section:
                        continue
                    if item == "distinct files" and not repeated:
                        continue
                    if section in ("requests", "answers") and item != "ok" and not count:
                        continue
                    rows.append([names[item], count])
                if rows:
                    summary_heading("File attempts" if section == "file records" and repeated else title,
                                    ["Item", "Count"])
                    for row in rows:
                        append(summary, row, capped=False)

        try:
            for index, report in enumerate(reports):
                if index == 1 and not recovery:
                    workbook.create_sheet("Details>>>")
                sheet = workbook.create_sheet(report.title)
                sheet.freeze_panes = "A2"
                widths = {"file_id": 10, "file_path": 32, "pages": 14, "file_result": 16, "notes": 60}
                for number, column in enumerate(report.headers[:EXCEL_MAX_COLUMNS], 1):
                    column_width = widths.get(column, 32 if column.startswith("llm_") else 22)
                    sheet.column_dimensions[get_column_letter(number)].width = max(
                        column_width, min(60, max(_header_line_widths(column)) + 3)
                    )
                cut_columns = len(report.headers) > EXCEL_MAX_COLUMNS
                kept_columns = EXCEL_MAX_COLUMNS - 1 if cut_columns else len(report.headers)
                if cut_columns:
                    omitted = report.headers[kept_columns:]
                    names = ", ".join(preview(name, 60) for name in omitted[:5])
                    if len(omitted) > 5:
                        names += f", and {len(omitted) - 5} more (see the CSV header)"
                    summary_notices.append((report.title,
                        f"{len(omitted)} columns omitted: {names} (columns {kept_columns + 1}-{len(report.headers)})."))
                    notices.append(
                        f"{report.title}: {len(omitted)} columns omitted: {names}. "
                        f"Original columns {kept_columns + 1}-{len(report.headers)}; "
                        f"first {kept_columns} retained. Full columns: {full_data(report.title)}"
                    )

                def width(row: Sequence[Any], cut_columns: bool = cut_columns) -> list[Any]:
                    return [*row[: EXCEL_MAX_COLUMNS - 1], COLUMN_OMISSION_MARKER] if cut_columns else list(row)

                append(sheet, width(report.headers), header=True, headers=report.headers)
                pending: list[Any] | None = None
                kept = 0
                total = 0
                for row in report.rows():
                    total += 1
                    if total < EXCEL_MAX_ROWS:
                        append(sheet, width(row), headers=report.headers, csv_record=total)
                        kept += 1
                    elif total == EXCEL_MAX_ROWS:
                        pending = row
                    elif total == EXCEL_MAX_ROWS + 1:
                        append(sheet, [ROW_OMISSION_MARKER] * min(len(report.headers), EXCEL_MAX_COLUMNS))
                if total == EXCEL_MAX_ROWS and pending is not None:
                    append(sheet, width(pending), headers=report.headers, csv_record=total)
                elif total > EXCEL_MAX_ROWS:
                    summary_notices.append((report.title, f"{total - kept} rows omitted; {kept} retained."))
                    notices.append(
                        f"{report.title}: {total - kept} rows cut after {kept} (full data: {full_data(report.title)})"
                    )
                last_column = get_column_letter(min(len(report.headers), EXCEL_MAX_COLUMNS))
                sheet.auto_filter.ref = f"A1:{last_column}{min(total, EXCEL_MAX_ROWS) + 1}"
            if counts["cells"]:
                notices.append(f"{counts['cells']} cells contain shortened text across the workbook")
            for (title, number, column), count in cut_fields.items():
                summary_notices.append((title,
                    f"Text shortened: {preview(column)} (column {number}), "
                    f"{count} cell" + ("s." if count != 1 else ".")))
                notices.append(
                    f"{title}: text shortened in {count} cell(s), column {number} "
                    f"({preview(column)}). Full text: {full_data(title)}"
                )
            for number, line in enumerate(introduction()):
                summary_line(line, bold=number == 0)
            count_sections()
            if saved_csvs:
                append(summary, [], capped=False)
                summary_line("Complete CSV reports, saved beside this workbook", bold=True)
                append(summary, ["Report", "File"], capped=False, header=True)
                for title, filename in saved_csvs.items():
                    append(summary, [title, filename], capped=False)
            if outcome.failed:
                summary_heading("Reports not saved", ["Report", "Reason"])
                for failure in outcome.failed:
                    append(summary, [failure["report"], failure["error"]], capped=False, red=True)
                summary_line("Retry export or save reports elsewhere in the app.")
            if not saved_csvs:
                summary_line("No CSV reports were saved. The data remains in the run database.")
            if summary_notices:
                summary_heading("Workbook omissions", ["Excel sheet", "Omitted"])
                for title, description in summary_notices:
                    if title not in saved_csvs:
                        description += " CSV unavailable; retained database. Retry export."
                    append(summary, [title, description], capped=False, red=True)
                if counts["cells"]:
                    summary_line(
                        "Use the listed CSVs for full text."
                        if all(title in saved_csvs for title, _, _ in cut_fields)
                        else "Full text for fields without a CSV remains in the run database."
                    )
                    summary_line("Record and column numbers in a marked cell locate its full value. "
                                 "Record 1 is the first entry after the CSV header; entries may contain line breaks.")
            else:
                append(summary, [], capped=False)
                summary_line(WORKBOOK_COMPLETE_LINE)
            if counts["substitutions"]:
                summary_heading("Text replacements", ["Item", "Count"])
                append(summary, ["Characters Excel cannot display", counts["substitutions"]], capped=False, red=True)
            archive = ZipFile(get_safe_path(target), "w", ZIP_DEFLATED, allowZip64=True)
            try:
                workbook.properties.modified = datetime.now(UTC).replace(tzinfo=None)
                ExcelWriter(workbook, archive).write_data()
            except BaseException:
                try:
                    archive.close()
                except (OSError, ValueError) as exc:
                    exporter_logger.warning("Could not close failed workbook archive: %s", exc)
                raise
            else:
                archive.close()
            outcome.notices.extend(notices)
            if counts["substitutions"]:
                outcome.notices.append(f"{counts['substitutions']} XML-incompatible characters replaced")
            for line in notices:
                exporter_logger.warning(line)
        finally:
            for sheet in workbook.worksheets:
                assert isinstance(sheet, WriteOnlyWorksheet)
                try:
                    if not sheet.closed:
                        sheet.close()
                    writer = getattr(sheet, "_writer", None)
                    assert writer is None or isinstance(writer, WorksheetWriter)
                    if writer is not None:
                        worksheet_path = writer.out
                        assert isinstance(worksheet_path, str)
                        if Path(get_safe_path(Path(worksheet_path))).exists():
                            writer.cleanup()
                except (OSError, ValueError):
                    exporter_logger.warning("Could not clean up a temporary worksheet")
            try:
                workbook.close()
            except (OSError, ValueError) as exc:
                exporter_logger.warning("Could not clean up workbook: %s", exc)

    def _export(self, directory: Path, timestamp: str | None, workbook: bool) -> ExportOutcome:
        directory = directory.resolve()
        outcome = ExportOutcome(str(self.database_path.resolve()), str(directory))
        if not Path(get_safe_path(self.database_path)).is_file():
            raise ConfigurationError(f"Cannot export missing database: {self.database_path}")
        con = sqlite3.connect(get_safe_path(self.database_path))
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN")
            config = self._configuration(con)
            reports = self._reports(con, config)
            stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
            Path(get_safe_path(directory)).mkdir(parents=True, exist_ok=True)
            base_stamp = stamp
            suffix = 2
            while (
                any(Path(get_safe_path(directory / f"{r.stem}_{stamp}.csv")).exists() for r in reports)
                or Path(get_safe_path(directory / f"database_export_{stamp}.xlsx")).exists()
                or Path(get_safe_path(directory / f"recovery_database_export_{stamp}.xlsx")).exists()
            ):
                stamp = f"{base_stamp}-{suffix}"
                suffix += 1
            for report in reports:
                try:
                    path = self._atomic_write(
                        directory / f"{report.stem}_{stamp}.csv",
                        lambda path, report=report: self._write_csv(path, report.headers, report.rows()),
                    )
                    outcome.saved.append({"report": report.title, "format": "csv", "path": str(path)})
                except Exception as exc:
                    outcome.failed.append(
                        {
                            "report": report.title,
                            "format": "csv",
                            "category": error_category(exc),
                            "error": replace_lone_surrogates(str(exc)),
                        }
                    )
            if workbook:
                try:
                    summary = self._summary(con, config)
                    path = self._atomic_write(
                        directory / f"database_export_{stamp}.xlsx",
                        lambda path: self._workbook(path, reports, summary, outcome),
                    )
                    outcome.saved.append({"report": "Workbook", "format": "xlsx", "path": str(path)})
                except Exception as exc:
                    outcome.failed.append(
                        {
                            "report": "Workbook",
                            "format": "xlsx",
                            "category": error_category(exc),
                            "error": replace_lone_surrogates(str(exc)),
                        }
                    )
                    if error_category(exc) == "data":
                        self._recovery(con, directory, stamp, outcome)
        except ConfigurationError:
            raise
        except Exception as exc:
            outcome.failed.append(
                {
                    "report": "Export",
                    "format": "all",
                    "category": error_category(exc),
                    "error": replace_lone_surrogates(str(exc)),
                }
            )
        finally:
            con.close()
        if outcome.failed:
            raise ExportError(outcome)
        return outcome

    def _recovery(self, con: sqlite3.Connection, directory: Path, stamp: str, outcome: ExportOutcome) -> None:
        reports = []
        for table in ("file_registry", "page_log", "llm_requests", "llm_answers", "run_configuration"):
            headers = [row[1] for row in con.execute(f"PRAGMA table_info({table})")]

            def rows(table: str = table) -> Iterator[list[Any]]:
                yield from (list(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY 1"))

            reports.append(Report("Raw " + table, "", headers, rows))
        try:
            path = self._atomic_write(
                directory / f"recovery_database_export_{stamp}.xlsx",
                lambda path: self._workbook(
                    path,
                    reports,
                    [["export", "Recovery only: normal Results could not be produced", None]],
                    outcome,
                    recovery=True,
                ),
            )
            outcome.recovery = True
            outcome.saved.append({"report": "Recovery workbook", "format": "xlsx", "path": str(path)})
        except Exception as exc:
            outcome.failed.append(
                {
                    "report": "Recovery workbook",
                    "format": "xlsx",
                    "category": error_category(exc),
                    "error": replace_lone_surrogates(str(exc)),
                }
            )

    def export_all_formats(self, output_directory_path: Path) -> ExportOutcome:
        return self._export(output_directory_path, None, True)

    def export_csv(self, output_directory_path: Path, timestamp: str | None = None) -> ExportOutcome:
        return self._export(output_directory_path, timestamp, False)
