# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import logging
import hashlib
import json
import os
import threading
import time
import traceback
from pathlib import Path

import windows_shell
from config_validator import AI_TAB_FIELDS, NON_IDENTITY_SETTINGS, Settings, parse_output_columns
from fs_utils import get_safe_path, humanize_paths
from schemas import ConfigurationError, RunConfiguration, exception_message
from to_jpeg_converter import ToJpegConverter
from range_parsers import PageRangeSelector, VideoSelector
from llm_client import LLMClient
from db_controller import SQLiteDatabaseController
from media_classifier import MediaClassifier
from batch_orchestrator import BatchOrchestrator
from data_exporter import SQLiteDataExporter, ExportError

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


def _spell_configuration_value(value) -> str:
    if isinstance(value, tuple):
        return ", ".join(value) or "-"
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value) or "-"


def run_configuration_of(settings: Settings) -> RunConfiguration:
    if not settings.ENABLE_LLM_INFERENCE:
        return RunConfiguration(False, "", ())
    return RunConfiguration(True, str(settings.LLM_OUTPUT_MODE),
                            tuple(parse_output_columns(settings.LLM_OUTPUT_COLUMNS)))


def processing_identity(settings: Settings, llm: LLMClient | None) -> tuple[str, str]:
    values = {name: getattr(settings, name) for name in Settings.model_fields
              if name not in NON_IDENTITY_SETTINGS | AI_TAB_FIELDS | {"INPUT_FOLDER_PATH"}}
    inactive_mode = "SAMPLING" if settings.VIDEO_MODE == "SUMMARY" else "SUMMARY"
    values = {name: value for name, value in values.items()
              if not name.startswith(f"VIDEO_{inactive_mode}_")}
    for name in ("IMAGE_RANGE", "DOCUMENT_RANGE", "ANIMATION_RANGE"):
        values[name] = PageRangeSelector(getattr(settings, name)).segments
    values["VIDEO_RANGE"] = VideoSelector(settings.VIDEO_RANGE).segments
    if llm is not None:
        values["ai"] = llm.processing_semantics()
    root = json.dumps(os.path.normcase(str(settings.INPUT_FOLDER_PATH.resolve())), ensure_ascii=True)
    canonical = json.dumps(values, sort_keys=True, ensure_ascii=True)
    return root, hashlib.sha256(canonical.encode("ascii")).hexdigest()


def describe_configuration_change(recorded: RunConfiguration,
                                  current: RunConfiguration) -> str:
    return "; ".join(
        f"{name}: {_spell_configuration_value(before)} -> {_spell_configuration_value(after)}"
        for name, before, after in zip(RunConfiguration._fields, recorded, current, strict=True)
        if before != after)


class ProcessorCore:

    def __init__(self, settings: Settings, abort_flag: threading.Event, on_progress=None):
        self.settings = settings
        self.abort_flag = abort_flag
        self.on_progress = on_progress

        Path(get_safe_path(self.settings.OUTPUT_FOLDER_PATH)).mkdir(parents=True, exist_ok=True)

        safe_run_folder = Path(get_safe_path(self.settings.CURRENT_RUN_FOLDER))
        if self.settings.START_OVER and safe_run_folder.exists() and any(safe_run_folder.iterdir()):
            from datetime import datetime

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

        current = run_configuration_of(self.settings)
        recorded = self.database_controller.get_run_configuration()
        if recorded is not None and recorded != current:
            self.database_controller.close()
            raise ConfigurationError(
                f"i18n:err_resume_configuration_changed|{describe_configuration_change(recorded, current)}",
                setting_field="START_OVER")
        self.database_controller.record_run_configuration(*current)

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
        try:
            self.database_controller.ensure_run_identity(*processing_identity(self.settings, self.llm))
        except Exception:
            self.database_controller.close()
            raise

        def range_selector(selector_type, field):
            try:
                return selector_type(getattr(self.settings, field))
            except ConfigurationError as error:
                error.setting_field = field
                raise

        self.router = MediaClassifier(
            self.settings,
            self.converter,
            self.settings.CURRENT_RUN_FOLDER,
            range_selector(PageRangeSelector, "DOCUMENT_RANGE"),
            range_selector(PageRangeSelector, "IMAGE_RANGE"),
            range_selector(PageRangeSelector, "ANIMATION_RANGE"),
            range_selector(VideoSelector, "VIDEO_RANGE"),
        )

        self.orchestrator = BatchOrchestrator(
            self.settings, self.database_controller, self.router, self.system_logger, self.llm
        )

        self.exporter = SQLiteDataExporter(self.db_path)

    def shutdown(self):
        if hasattr(self, "database_controller"):
            self.database_controller.close()

    def run(self):
        run_failed = False
        run_refusal = {}
        try:
            self.settings.apply_library_limits()

            self.orchestrator.execute_batch_processing_loop(
                abort_flag=self.abort_flag, on_progress=self.on_progress
            )

        except Exception as e:
            run_failed = True
            if isinstance(e, ConfigurationError) and e.detail_key:
                run_refusal = exception_message(e)
            error_msg = f"FATAL ERROR during runtime: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)

        export_outcome = None
        export_failure: dict[str, object] | None = None
        try:
            export_outcome = self.exporter.export_all_formats(self.settings.TECH_FOLDER_PATH)
        except ExportError as e:
            export_outcome = e.outcome
            logger.error(f"Report export incomplete: {e}")
        except Exception as e:
            logger.error(f"Report export failed: {e}\n{traceback.format_exc()}")
            export_failure = {"status": "error", "export_state": "unknown",
                              "message_key": "err_export_outcome_unknown", "detail": str(e),
                              "path": str(self.settings.TECH_FOLDER_PATH)}
        finally:
            if self.on_progress:
                result = export_outcome.as_dict() if export_outcome is not None else export_failure
                if result is not None:
                    from routes.export_api import number_export_result, remember_export_source
                    result.pop("database", None)
                    result["type"] = "export_result"
                    if export_outcome is not None:
                        result["export_state"] = "completed"
                    try:
                        result["recovery_id"] = remember_export_source(self.db_path)
                    except OSError:
                        result["recovery_id"] = None
                    self.on_progress(number_export_result(result))
                if run_failed:
                    self.on_progress({"type": "failed", **run_refusal})
                elif self.abort_flag is not None and self.abort_flag.is_set():
                    self.on_progress({"type": "aborted"})
                else:
                    self.on_progress({"type": "progress", "value": 100})
                    self.on_progress({"type": "done"})
            self.shutdown()
