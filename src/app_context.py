# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import logging
import threading
import time
import traceback
from pathlib import Path

import windows_shell
from config_validator import Settings
from fs_utils import get_safe_path, humanize_paths
from to_jpeg_converter import ToJpegConverter
from range_parsers import PageRangeSelector, VideoSelector
from llm_client import LLMClient
from db_controller import SQLiteDatabaseController
from media_classifier import MediaClassifier
from batch_orchestrator import BatchOrchestrator
from data_exporter import SQLiteDataExporter

logger = logging.getLogger(__name__)

ARCHIVE_RENAME_ATTEMPTS = 10
ARCHIVE_RENAME_RETRY_DELAY_SECONDS = 0.25


def _format_os_error(error: OSError) -> str:
    win = getattr(error, "winerror", None)
    code = f"[WinError {win}]" if win else (
        f"[Error {error.errno}]" if error.errno else ""
    )
    message = error.strerror or str(error)
    paths = ""
    if error.filename:
        paths = f": {error.filename}"
        if error.filename2:
            paths += f" -> {error.filename2}"
    return humanize_paths(f"{code} {message}{paths}".strip())


class ProcessorCore:

    def __init__(self, settings: Settings, abort_flag: threading.Event, on_progress=None):
        self.settings = settings
        self.abort_flag = abort_flag
        self.on_progress = on_progress

        Path(get_safe_path(self.settings.OUTPUT_FOLDER_PATH)).mkdir(parents=True, exist_ok=True)

        safe_run_folder = Path(get_safe_path(self.settings.CURRENT_RUN_FOLDER))
        if self.settings.START_OVER and safe_run_folder.exists() and any(safe_run_folder.iterdir()):
            from datetime import datetime
            from schemas import ConfigurationError

            timestamp = (
                datetime.now().isoformat(timespec="seconds").replace(":", "-").replace("T", "_")
            )
            archived_name = f"old_{self.settings.CURRENT_RUN_FOLDER.name}_{timestamp}"
            archive_target = self.settings.OUTPUT_FOLDER_PATH / archived_name
            suffix_counter = 2
            while Path(get_safe_path(archive_target)).exists():
                archive_target = (
                    self.settings.OUTPUT_FOLDER_PATH / f"{archived_name}-{suffix_counter}"
                )
                suffix_counter += 1

            for attempt in range(1, ARCHIVE_RENAME_ATTEMPTS + 1):
                try:
                    safe_run_folder.rename(get_safe_path(archive_target))
                    if attempt > 1:
                        logger.info("archive rename succeeded on attempt %d", attempt)
                    break
                except OSError as rename_error:
                    blocking_error = rename_error

                    if attempt == 1 and windows_shell.is_available():
                        try:
                            windows_shell.rename_folder_like_explorer(
                                self.settings.CURRENT_RUN_FOLDER, archive_target
                            )
                        except OSError as shell_error:
                            blocking_error = shell_error
                        else:
                            logger.info(
                                "archive rename needed the shell fallback; a "
                                "folder was open in Explorer"
                            )
                            break

                    last_attempt = attempt == ARCHIVE_RENAME_ATTEMPTS
                    if last_attempt or self.abort_flag.is_set():
                        raise ConfigurationError(
                            f"i18n:err_archive_locked|{_format_os_error(blocking_error)}"
                        ) from blocking_error
                    time.sleep(ARCHIVE_RENAME_RETRY_DELAY_SECONDS)

        Path(get_safe_path(self.settings.TECH_FOLDER_PATH)).mkdir(parents=True, exist_ok=True)
        Path(get_safe_path(self.settings.CURRENT_RUN_FOLDER)).mkdir(parents=True, exist_ok=True)

        self.db_path = self.settings.TECH_FOLDER_PATH / "application_state.db"
        self.database_controller = SQLiteDatabaseController(self.db_path)

        import central_logger
        central_logger.setup_logging(self.settings.LOGGING_LEVEL)
        self.system_logger = central_logger.system_logger

        self.converter = ToJpegConverter(
            self.settings.JPEG_QUALITY,
            self.settings.MAX_DIMENSION,
            self.settings.MAX_FILE_SIZE_KB,
            self.settings.LOWEST_QUALITY,
            self.settings.WHITE_BACKGROUND,
        )

        self.llm = LLMClient(self.settings) if self.settings.ENABLE_LLM_INFERENCE else None

        self.router = MediaClassifier(
            self.settings,
            self.converter,
            self.settings.CURRENT_RUN_FOLDER,
            PageRangeSelector(self.settings.DOCUMENT_RANGE),
            PageRangeSelector(self.settings.IMAGE_RANGE),
            PageRangeSelector(self.settings.ANIMATION_RANGE),
            VideoSelector(self.settings.VIDEO_RANGE),
        )

        self.orchestrator = BatchOrchestrator(
            self.settings, self.database_controller, self.router, self.system_logger, self.llm
        )

        self.exporter = SQLiteDataExporter(self.db_path)

    def shutdown(self):
        if hasattr(self, "database_controller"):
            self.database_controller.close()
        if hasattr(self, "exporter"):
            self.exporter.close()

    def run(self):
        run_failed = False
        try:
            self.settings.apply_library_limits()

            self.orchestrator.execute_batch_processing_loop(
                abort_flag=self.abort_flag, on_progress=self.on_progress
            )

            self.exporter.export_all_formats(self.settings.TECH_FOLDER_PATH)

        except Exception as e:
            run_failed = True
            error_msg = f"FATAL ERROR during runtime: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
        finally:
            if self.on_progress:
                if run_failed:
                    self.on_progress({"type": "failed"})
                elif self.abort_flag is not None and self.abort_flag.is_set():
                    self.on_progress({"type": "aborted"})
                else:
                    self.on_progress({"type": "progress", "value": 100})
                    self.on_progress({"type": "done"})
            self.shutdown()
