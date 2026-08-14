# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generator, List, Optional
from fs_utils import format_hms_for_filename, sanitize_filename_prefix
from schemas import Status, PageResult, FileSummary

class BaseMediaPipeline(ABC):

    def __init__(self, settings, file_id: int, input_path: Path, relative_path: str, original_extension: str, output_folder: Path):
        self.settings = settings
        self.file_id = file_id
        self.input_path = input_path
        self.relative_path = relative_path
        self.original_extension = original_extension
        self.output_folder = output_folder

    @abstractmethod
    def process(self) -> Generator[PageResult, None, FileSummary]:
        pass

    def get_filename(self, page_number: int, capture_seconds: Optional[float] = None) -> str:
        prefix = sanitize_filename_prefix(
            Path(self.relative_path).stem,
            self.settings.OUTPUT_FILENAME_PREFIX_LENGTH,
        )
        stem_part = f"{self.file_id}_{prefix}" if prefix else f"{self.file_id}"
        name = f"{stem_part}_page_{page_number}"
        if capture_seconds is not None and self.settings.OUTPUT_FILENAME_TIMESTAMPS:
            name += f"_t{format_hms_for_filename(capture_seconds)}"
        return f"{name}.jpg"

    def get_output_path(self, filename: str) -> Path:
        return self.output_folder / filename

    def create_failure_summary(self, error_message: str) -> FileSummary:
        return FileSummary(
            total_discovered_pages=0,
            applied_range_string="",
            range_status_code=Status.FAILURE.value,
            final_aggregate_status=Status.FAILURE.value,
            final_aggregate_comment=error_message
        )

    def abort_pipeline(self, error_message: str, dummy_comment: Optional[str] = None) -> Generator[PageResult, None, FileSummary]:
        if dummy_comment is None:
            dummy_comment = error_message

        yield PageResult(page_number=1, output_filename="", success=Status.FAILURE.value, comment=dummy_comment)
        return self.create_failure_summary(error_message)

    def finalize_results(
        self, expected_count: int, ok_count: int, skipped_count: int, failed_count: int,
        total_pages: int, range_string: str, range_status: str, error_summaries: List[str]
    ) -> FileSummary:

        valid_results = ok_count + skipped_count

        if expected_count == 0:
            file_success = Status.SKIPPED.value
            file_comment = "Configured range does not overlap this file; nothing was extracted"
        elif valid_results == expected_count and expected_count > 0:
            file_success = Status.OK.value
            if skipped_count > 0:
                file_comment = f"Processed {expected_count} candidates: {ok_count} saved, {skipped_count} skipped (static/duplicate)"
            else:
                file_comment = f"Successfully saved all {expected_count} requested frames"
        elif valid_results > 0:
            file_success = Status.PARTIAL_FAILURE.value
            file_comment = f"Target {expected_count} candidates: {ok_count} saved, {skipped_count} skipped, {failed_count} failed"
        else:
            file_success = Status.FAILURE.value
            file_comment = f"All {expected_count} candidates failed to process"

        unique_errors = list(dict.fromkeys(error_summaries))
        if unique_errors:
            file_comment += f" | Details: {'; '.join(unique_errors)}"

        return FileSummary(
            total_discovered_pages=total_pages,
            applied_range_string=range_string,
            range_status_code=range_status,
            final_aggregate_status=file_success,
            final_aggregate_comment=file_comment
        )
