# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import traceback
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING
from collections.abc import Generator

if TYPE_CHECKING:
    from range_parsers import PageRangeSelector, VideoSelector

from fs_utils import get_safe_path
from schemas import Status, PageResult, FileSummary
from pipelines.video import VideoPipeline
from pipelines.animation import AnimationPipeline
from pipelines.document import DocumentPipeline
from pipelines.static_image import StaticImagePipeline
from to_jpeg_converter import ToJpegConverter

ANIMATED_IMAGE_EXTENSIONS = frozenset([".gif", ".webp"])
IMAGE_EXTENSIONS = frozenset([".jpeg", ".jpg", ".jpe", ".jfif", ".png", ".bmp", ".dib",
                              ".tif", ".tiff", ".heic", ".heif", ".avif"])
PDF_EXTENSIONS = frozenset([".pdf"])
VIDEO_EXTENSIONS = frozenset([".mp4", ".mov", ".avi", ".mkv", ".wmv", ".webm"])


class MediaClassifier:

    def __init__(
        self,
        application_settings,
        universal_image_converter: ToJpegConverter,
        master_output_folder_path: Path,
        document_range_selector: "PageRangeSelector",
        image_range_selector: "PageRangeSelector",
        animation_range_selector: "PageRangeSelector",
        video_time_selector: "VideoSelector",
    ):
        self.settings = application_settings
        self.converter = universal_image_converter
        self.output_folder = master_output_folder_path
        self.document_selector = document_range_selector
        self.image_selector = image_range_selector
        self.animation_selector = animation_range_selector
        self.video_selector = video_time_selector

    @staticmethod
    def relative_or_orphan(target_path: Path, root_folder: Path) -> tuple[str, bool]:
        target_path = Path(target_path)
        try:
            return str(target_path.relative_to(Path(root_folder))), False
        except ValueError:
            path_hash = hashlib.md5(
                str(target_path.resolve()).encode("utf-8"), usedforsecurity=False
            ).hexdigest()[:8]
            return f"unresolved_orphaned_{path_hash}_{target_path.name}", True

    def evaluate_and_route(
        self,
        unique_file_id: int,
        raw_input_path: str | Path,
        raw_root_input_folder: str | Path,
    ) -> tuple[str, str, str, Generator[PageResult, None, FileSummary], bool]:

        try:
            target_path = Path(str(raw_input_path)) if raw_input_path else Path("INVALID_FILE_PATH")
            root_folder = Path(str(raw_root_input_folder)) if raw_root_input_folder else Path(".")

            safe_target = Path(get_safe_path(target_path))
            if safe_target.exists() and safe_target.is_file() and safe_target.stat().st_size == 0:
                relative_path, is_orphaned = self.relative_or_orphan(target_path, root_folder)
                return self._build_rejection_payload(
                    relative_path,
                    target_path.suffix.lower(),
                    "File is completely empty (0 bytes).",
                    is_orphaned,
                )
        except Exception:
            return self._build_rejection_payload(
                "INVALID_PATH", "unknown", "Paths totally invalid.", False
            )

        relative_path, is_orphaned = self.relative_or_orphan(target_path, root_folder)
        fallback_msg = (
            f" [Orphaned path fallback. Original location: {target_path.absolute()}]"
            if is_orphaned
            else ""
        )

        extension = target_path.suffix.lower()

        try:
            if extension in VIDEO_EXTENSIONS:
                pipeline = VideoPipeline(
                    self.settings,
                    unique_file_id,
                    target_path,
                    relative_path,
                    extension,
                    self.output_folder,
                    self.converter,
                    self.video_selector,
                )
                return relative_path, extension, "VideoPipeline", pipeline.process(), is_orphaned
            elif extension in ANIMATED_IMAGE_EXTENSIONS:
                pipeline = AnimationPipeline(
                    self.settings,
                    unique_file_id,
                    target_path,
                    relative_path,
                    extension,
                    self.output_folder,
                    self.converter,
                    self.animation_selector,
                )
                return relative_path, extension, "AnimationPipeline", pipeline.process(), is_orphaned
            elif extension in PDF_EXTENSIONS:
                pipeline = DocumentPipeline(
                    self.settings,
                    unique_file_id,
                    target_path,
                    relative_path,
                    extension,
                    self.output_folder,
                    self.converter,
                    self.document_selector,
                )
                return relative_path, extension, "DocumentPipeline", pipeline.process(), is_orphaned
            elif extension in IMAGE_EXTENSIONS:
                pipeline = StaticImagePipeline(
                    self.settings,
                    unique_file_id,
                    target_path,
                    relative_path,
                    extension,
                    self.output_folder,
                    self.converter,
                    self.image_selector,
                )
                return relative_path, extension, "StaticImagePipeline", pipeline.process(), is_orphaned
            else:
                return self._build_rejection_payload(
                    relative_path,
                    extension,
                    f"Unsupported file extension: {extension}{fallback_msg}",
                    is_orphaned,
                )

        except Exception as routing_crash:
            return self._build_rejection_payload(
                relative_path,
                extension,
                f"Internal routing crash: {routing_crash!s}{fallback_msg}\n{traceback.format_exc()}",
                is_orphaned,
            )

    def _build_rejection_payload(
        self, relative_path: str, extension: str, error_msg: str, is_orphaned: bool
    ) -> tuple[str, str, str, Generator[PageResult, None, FileSummary], bool]:
        def rejection_generator() -> Generator[PageResult, None, FileSummary]:
            yield PageResult(
                page_number=1, output_filename="", success=Status.FAILURE.value, comment=error_msg
            )
            return FileSummary(
                total_discovered_pages=0,
                applied_range_string="",
                range_status_code=Status.FAILURE.value,
                final_aggregate_status=Status.FAILURE.value,
                final_aggregate_comment=error_msg,
            )

        return relative_path, extension, "Rejected", rejection_generator(), is_orphaned
