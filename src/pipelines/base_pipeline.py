# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from abc import ABC, abstractmethod
from pathlib import Path
from collections.abc import Generator
from fs_utils import format_hms_for_filename, sanitize_filename_prefix
from schemas import Status, PageResult, FileSummary

class BaseMediaPipeline(ABC):

    def __init__(self, settings, file_id: int, input_path: Path, relative_path: str,
                 original_extension: str, output_folder: Path):
        self.settings = settings
        self.file_id = file_id
        self.input_path = input_path
        self.relative_path = relative_path
        self.original_extension = original_extension
        self.output_folder = output_folder

    @abstractmethod
    def process(self) -> Generator[PageResult, None, FileSummary]:
        pass

    def get_filename(self, page_number: int, capture_seconds: float | None = None) -> str:
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
            total_pages=0,
            page_range="",
            range_status=Status.FAILURE.value,
            file_to_jpegs_comment=error_message
        )

    def abort_pipeline(self, error_message: str, dummy_comment: str) -> Generator[PageResult, None, FileSummary]:
        yield PageResult(page_number=1, output_filename="", success=Status.FAILURE.value, comment=dummy_comment)
        return self.create_failure_summary(error_message)

    def finalize_results(
        self, expected_count: int, ok_count: int, skipped_count: int, failed_count: int,
        total_pages: int, range_string: str, range_status: str, error_summaries: list[str]
    ) -> FileSummary:

        valid_results = ok_count + skipped_count

        if expected_count == 0:
            file_comment = "Configured range does not overlap this file; nothing was extracted"
        elif valid_results == expected_count:
            if skipped_count > 0:
                file_comment = (f"Processed {expected_count} candidates: {ok_count} saved, "
                                f"{skipped_count} skipped (static/duplicate)")
            else:
                file_comment = f"Successfully saved all {expected_count} requested frames"
        elif valid_results > 0:
            file_comment = (f"Target {expected_count} candidates: {ok_count} saved, "
                            f"{skipped_count} skipped, {failed_count} failed")
        else:
            file_comment = f"All {expected_count} candidates failed to process"

        unique_errors = list(dict.fromkeys(error_summaries))
        if unique_errors:
            file_comment += f" | Details: {'; '.join(unique_errors)}"

        return FileSummary(
            total_pages=total_pages,
            page_range=range_string,
            range_status=range_status,
            file_to_jpegs_comment=file_comment
        )
