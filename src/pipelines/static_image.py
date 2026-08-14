# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


from pathlib import Path
from typing import Generator, TYPE_CHECKING

if TYPE_CHECKING:
    from range_parsers import PageRangeSelector

from fs_utils import get_safe_path
from schemas import Status, RangeStatus, PageResult, FileSummary
from pipelines.base_pipeline import BaseMediaPipeline
from to_jpeg_converter import ToJpegConverter, open_supported_image


class StaticImagePipeline(BaseMediaPipeline):

    def __init__(
        self,
        settings,
        file_id: int,
        input_path: Path,
        relative_path: str,
        original_extension: str,
        output_folder: Path,
        converter: ToJpegConverter,
        page_selector: "PageRangeSelector",
    ):
        super().__init__(settings, file_id, input_path, relative_path, original_extension, output_folder)
        self.converter = converter
        self.page_selector = page_selector

    def process(self) -> Generator[PageResult, None, FileSummary]:
        ok_count = skipped_count = failed_count = 0
        error_summaries = []

        try:
            with open_supported_image(get_safe_path(self.input_path)) as pil_image:

                total_pages = getattr(pil_image, "n_frames", 1)

                mpo_note = None
                if pil_image.format == "MPO" and total_pages > 1:
                    total_pages = 1
                    mpo_note = "MPO auxiliary image ignored"

                if total_pages == 0:
                    return (
                        yield from self.abort_pipeline(
                            "Empty file (0 frames)", "Empty file (0 frames)"
                        )
                    )

                if total_pages == 1:
                    indices, range_status, range_string = [0], RangeStatus.OK.value, "1"
                else:
                    range_result = self.page_selector.calculate_indices(total_pages)
                    indices, range_status = range_result.indices, range_result.status
                    if range_result.details:
                        error_summaries.append(range_result.details)
                    range_string = self.page_selector.format_range_string(indices, truncate=False)

                if not indices:
                    return self.finalize_results(
                        0, 0, 0, 0, total_pages, "", range_status,
                        error_summaries + ["Skipped by range limits"],
                    )

                for current_index in indices:
                    page_number = current_index + 1
                    output_filename = self.get_filename(page_number)
                    detached_image_copy = None

                    try:
                        pil_image.seek(current_index)

                        detached_image_copy = pil_image.copy()

                        page_success, page_comment = self.converter.process_image(
                            detached_image_copy, self.get_output_path(output_filename)
                        )

                        if page_success == Status.OK.value:
                            ok_count += 1
                        else:
                            failed_count += 1
                            error_summaries.append(page_comment)

                    except Exception as extraction_error:
                        page_success, page_comment = (
                            Status.FAILURE.value,
                            f"Image error: {extraction_error}",
                        )
                        failed_count += 1
                        error_summaries.append(page_comment)

                    finally:
                        if detached_image_copy:
                            try:
                                detached_image_copy.close()
                            except Exception:
                                pass

                    if mpo_note:
                        page_comment = (
                            f"{page_comment}; {mpo_note}" if page_comment else mpo_note
                        )

                    yield PageResult(page_number, output_filename, page_success, page_comment)

            return self.finalize_results(
                len(indices),
                ok_count,
                skipped_count,
                failed_count,
                total_pages,
                range_string,
                range_status,
                error_summaries,
            )

        except Exception as file_read_error:
            return (
                yield from self.abort_pipeline(
                    f"Catastrophic Image error: {file_read_error}", str(file_read_error)
                )
            )
