# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import logging
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Generator

if TYPE_CHECKING:
    from range_parsers import PageRangeSelector

from fs_utils import get_safe_path
from schemas import Status, RangeStatus, PageResult, FileSummary
from pipelines.base_pipeline import BaseMediaPipeline
from to_jpeg_converter import ToJpegConverter

logger = logging.getLogger(__name__)

try:
    import pypdfium2 as pdfium
except ImportError as err:
    pdfium = None
    pdf_err = str(err)
except Exception as unk_err:
    pdfium = None
    pdf_err = str(unk_err)


class DocumentPipeline(BaseMediaPipeline):

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
        if pdfium is None:
            logger.error(
                f"Cannot process {self.relative_path}: pypdfium2 is missing. Error: {pdf_err}"
            )
            return (yield from self.abort_pipeline(f"Library failure: {pdf_err}", pdf_err))

        ok_count = skipped_count = failed_count = 0
        error_summaries = []

        try:
            with pdfium.PdfDocument(get_safe_path(self.input_path)) as pdf_doc:

                total_pages = len(pdf_doc)

                logger.debug(
                    f"Successfully opened {self.relative_path}. Total pages detected: {total_pages}"
                )

                if total_pages == 0:
                    logger.warning(f"File is empty (0 pages): {self.relative_path}")
                    return (yield from self.abort_pipeline("Empty file (0 pages)", "Empty file"))

                range_result = self.page_selector.calculate_indices(total_pages)
                indices, range_status = range_result.indices, range_result.status
                if range_result.details:
                    error_summaries.append(range_result.details)

                max_pages = self.settings.DOCUMENT_MAX_PAGES

                if len(indices) > max_pages:
                    logger.warning(
                        f"Memory Cap Reached: Truncating {self.relative_path} from "
                        f"{len(indices)} requested pages down to {max_pages}."
                    )
                    indices = indices[:max_pages]
                    if range_status == RangeStatus.OK.value:
                        range_status = RangeStatus.TRUNCATED.value

                range_string = self.page_selector.format_range_string(indices, truncate=False)

                if not indices:
                    logger.info(f"Skipping {self.relative_path}: Range out of bounds.")
                    return self.finalize_results(
                        0, 0, 0, 0, total_pages, "", range_status,
                        [*error_summaries, "Skipped bounds"],
                    )

                logger.info(
                    f"Starting extraction of {len(indices)} pages from {self.relative_path}..."
                )

                for current_index in indices:
                    page_number = current_index + 1
                    output_filename = self.get_filename(page_number)

                    try:
                        pdf_page = pdf_doc[current_index]
                        try:
                            width_pt, height_pt = pdf_page.get_size()
                            longest_pt = max(width_pt, height_pt)

                            if longest_pt <= 0:
                                raise ValueError("Invalid PDF dimension (0 points).")

                            raw_safety_limit = float(self.settings.MAX_DIMENSION) / float(
                                longest_pt
                            )
                            safety_limit_scale = max(1, int(raw_safety_limit))

                            safe_scale = min(int(self.settings.PDF_SCALE), safety_limit_scale)

                            logger.debug(
                                f"[Page {page_number}] Scale Math - Longest Edge: {longest_pt}pt | "
                                f"Raw Limit: {raw_safety_limit:.2f} | Final Scale Used: {safe_scale}"
                            )

                            with pdf_page.render(scale=safe_scale, rotation=0).to_pil() as rendered:
                                page_success, page_comment = self.converter.process_image(
                                    rendered, self.get_output_path(output_filename)
                                )

                                if page_success == Status.OK.value:
                                    ok_count += 1
                                    logger.debug(
                                        f"Successfully rendered and saved page {page_number}."
                                    )
                                else:
                                    failed_count += 1
                                    error_summaries.append(page_comment)
                        finally:
                            pdf_page.close()

                    except Exception as render_err:
                        logger.error(
                            f"Failed rendering page {page_number} in {self.relative_path}: {render_err}",
                            exc_info=True,
                        )
                        page_success, page_comment = (
                            Status.FAILURE.value,
                            f"Render error: {render_err}",
                        )
                        failed_count += 1
                        error_summaries.append(page_comment)

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

        except Exception as doc_err:
            logger.critical(
                f"Catastrophic error opening PDF {self.relative_path}: {doc_err}", exc_info=True
            )
            return (yield from self.abort_pipeline(f"Document error: {doc_err}", str(doc_err)))
