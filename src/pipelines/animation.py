# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import logging
import numpy as np
from pathlib import Path
from typing import Generator, TYPE_CHECKING

if TYPE_CHECKING:
    from range_parsers import PageRangeSelector

from fs_utils import get_safe_path
from schemas import Status, RangeStatus, PageResult, FileSummary
from pipelines.base_pipeline import BaseMediaPipeline
from to_jpeg_converter import ToJpegConverter, is_frame_distinct, open_supported_image
from range_parsers import calculate_summary_indices

logger = logging.getLogger(__name__)


class AnimationPipeline(BaseMediaPipeline):

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
        last_saved_frame = None

        try:
            with open_supported_image(get_safe_path(self.input_path)) as pil_image:
                total_frames = getattr(pil_image, "n_frames", 1)

                if total_frames == 0:
                    return (yield from self.abort_pipeline("Empty file (0 frames)", "Empty file"))

                range_result = self.page_selector.calculate_indices(total_frames)
                pool_indices, range_status = range_result.indices, range_result.status
                if range_result.details:
                    error_summaries.append(range_result.details)

                if not pool_indices:
                    return self.finalize_results(
                        0, 0, 0, 0, total_frames, "", range_status,
                        error_summaries + ["Skipped by bounds"],
                    )

                target_count = self.settings.ANIMATION_TARGET_TOTAL_FRAMES
                extract_count = min(len(pool_indices), target_count)

                if len(pool_indices) > extract_count:
                    compression_message = f"Compressed: Selected {extract_count} candidates from {len(pool_indices)} requested"
                    error_summaries.append(compression_message)
                    logger.info(
                        f"[{self.file_id}] Budget Cap Reached: Compressing {len(pool_indices)} requested frames down to {extract_count}."
                    )

                    if range_status == RangeStatus.OK.value:
                        range_status = RangeStatus.TRUNCATED.value

                selection_indices = calculate_summary_indices(
                    0, len(pool_indices), extract_count
                ).indices
                target_indices = [pool_indices[i] for i in selection_indices]
                range_string = self.page_selector.format_range_string(
                    target_indices, truncate=False
                )
                sensitivity = self.settings.ANIMATION_SCENE_SENSITIVITY
                target_set = set(target_indices)

                for current_index in range(total_frames):
                    is_target_frame = current_index in target_set
                    frame_number = current_index + 1
                    output_filename = self.get_filename(frame_number)

                    try:
                        pil_image.seek(current_index)
                        pil_image.load()
                    except EOFError:
                        break
                    except Exception as decode_err:
                        logger.error(
                            f"Corrupt frame data at frame {frame_number} in {self.relative_path}: {decode_err}",
                            exc_info=True,
                        )
                        if is_target_frame:
                            failed_count += 1
                            error_summaries.append(f"Corrupt frame data: {decode_err}")
                            yield PageResult(
                                frame_number,
                                output_filename,
                                Status.FAILURE.value,
                                f"Corrupt frame data: {decode_err}",
                            )
                        else:
                            error_summaries.append(
                                f"Corrupt frame data at frame {frame_number}; extraction stopped early"
                            )
                        break

                    if not is_target_frame:
                        continue

                    frame = None

                    try:
                        frame = pil_image.convert("RGBA")
                        current_frame_array = np.array(frame)

                        if not is_frame_distinct(
                            current_frame_array, last_saved_frame, sensitivity
                        ):
                            skipped_count += 1
                            yield PageResult(
                                frame_number, output_filename, Status.SKIPPED.value, "Scene static"
                            )
                            continue

                        page_success, page_comment = self.converter.process_image(
                            frame, self.get_output_path(output_filename)
                        )
                        if page_success == Status.OK.value:
                            last_saved_frame = current_frame_array
                            ok_count += 1
                        else:
                            failed_count += 1
                            error_summaries.append(page_comment)

                        yield PageResult(frame_number, output_filename, page_success, page_comment)

                    except Exception as ext_err:
                        failed_count += 1
                        error_summaries.append(str(ext_err))
                        logger.error(
                            f"Failed processing frame {frame_number} in {self.relative_path}: {ext_err}",
                            exc_info=True,
                        )
                        yield PageResult(
                            frame_number, output_filename, Status.FAILURE.value, str(ext_err)
                        )
                    finally:
                        if frame and frame is not pil_image:
                            frame.close()

            return self.finalize_results(
                len(target_indices),
                ok_count,
                skipped_count,
                failed_count,
                total_frames,
                range_string,
                range_status,
                error_summaries,
            )

        except Exception as cat_err:
            logger.critical(
                f"Catastrophic error opening animation {self.relative_path}: {cat_err}",
                exc_info=True,
            )
            return (yield from self.abort_pipeline(f"Animation error: {cat_err}", str(cat_err)))
