# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import gc
from sqlalchemy.exc import SQLAlchemyError
from schemas import Status, FileSummary, ConfigurationError
from pathlib import Path

from fs_utils import get_safe_path, humanize_paths

class BatchOrchestrator:

    def __init__(self, settings_configuration, database_controller_instance,
                 file_router_instance, system_logger_instance, llm_network_client=None):
        self.settings = settings_configuration
        self.input_folder = self.settings.INPUT_FOLDER_PATH
        self.db = database_controller_instance
        self.router = file_router_instance
        self.logger = system_logger_instance
        self.llm_client = llm_network_client

    def _discover_files(self) -> list:
        safe_root = Path(get_safe_path(self.input_folder))
        return sorted(
            self.input_folder / p.relative_to(safe_root)
            for p in safe_root.rglob("*")
            if p.is_file()
        )

    def execute_batch_processing_loop(self, abort_flag=None, on_progress=None) -> None:
        current_id = self.db.get_highest_file_id()

        no_retry_values = [status.value for status in self.settings.NO_RETRY_STATUSES]
        processed_paths = self.db.get_successfully_processed_relative_paths(no_retry_values)

        self.logger.app_logger.info(
            f"Process starting. Next available ID: {current_id + 1}. "
            f"Skipping {len(processed_paths)} historically completed files.")

        all_files = self._discover_files()
        total_files = len(all_files)
        self.logger.app_logger.info(f"Filesystem scan complete. Total files discovered: {total_files}")

        self.logger.app_logger.info(
            f"Optimization: Applying {self.settings.JPEG_QUALITY}% JPEG quality "
            f"and {self.settings.MAX_DIMENSION}px dimension limit to Batch #1")

        if total_files == 0:
            self.logger.app_logger.warning("Input folder is empty. Ending process.")
            if on_progress:
                on_progress({"type": "progress", "value": 100})
            return

        files_since_gc = 0
        consecutive_llm_failures = 0

        for idx, input_path in enumerate(all_files):
            if abort_flag and abort_flag.is_set():
                self.logger.app_logger.warning("Pipeline execution aborted by user. Exiting batch loop cleanly.")
                break

            if on_progress:
                on_progress({"type": "progress", "value": int((idx / total_files) * 100)})

            file_started_in_db = False
            file_completed_in_db = False

            try:
                rel_path, _ = self.router.relative_or_orphan(input_path, self.input_folder)
                if rel_path in processed_paths:
                    continue

                next_id = current_id + 1
                rel_path, ext, pipeline_name, generator, is_orphaned = \
                    self.router.evaluate_and_route(next_id, input_path, self.input_folder)

                current_id = next_id
                self.db.handle_file_started(current_id, rel_path, ext, pipeline_name)
                file_started_in_db = True

                self.logger.log_file_started(current_id, rel_path, ext)

                file_summary = None
                try:
                    while True:
                        if abort_flag and abort_flag.is_set():
                            file_summary = FileSummary(
                                0, "", Status.FAILURE.value, Status.FAILURE.value,
                                "Aborted mid-extraction by user command.")
                            break

                        page_result = next(generator)
                        self.db.handle_frame_saved(current_id, page_result)
                        self.logger.log_frame_saved(current_id, page_result)
                except StopIteration as e:
                    file_summary = e.value

                if file_summary is not None and is_orphaned:
                    file_summary.final_aggregate_comment += humanize_paths(
                        f" [Orphaned path fallback. Original location: {input_path.absolute()}]"
                    )

                if file_summary is not None:
                    self.db.handle_file_completed(current_id, file_summary)
                    file_completed_in_db = True
                    self.logger.log_file_completed(current_id, file_summary)

                if abort_flag and abort_flag.is_set():
                    break

                if self.settings.ENABLE_LLM_INFERENCE and self.llm_client is not None:
                    valid_frames = self.db.get_successful_frame_paths(current_id, self.router.output_folder)

                    if not valid_frames:
                        self.logger.app_logger.warning(
                            f"[{current_id}] No valid frames to send to AI. Bypassing network call.")
                        self.db.handle_llm_completed(
                            current_id, "No images provided for network inference.",
                            "No valid frames extracted.", Status.FAILURE.value)
                        self.logger.log_llm_completed(current_id, Status.FAILURE.value, "No valid frames extracted.")
                    else:
                        self.logger.app_logger.info(
                            f"[{current_id}] Extraction complete. "
                            f"Executing LLM Inference on {len(valid_frames)} frame(s)...")

                        inference = self.llm_client.execute_network_inference(valid_frames, abort_flag=abort_flag)
                        self.db.handle_llm_completed(current_id, inference.answer, inference.error, inference.status)
                        self.logger.log_llm_completed(current_id, inference.status, inference.error)

                        if inference.status == Status.LLM_FAILED.value:
                            consecutive_llm_failures += 1
                            if consecutive_llm_failures >= self.settings.MAX_CONSECUTIVE_LLM_FAILURES:
                                abort_msg = (
                                    f"CIRCUIT BREAKER TRIPPED: AI Server failed "
                                    f"{self.settings.MAX_CONSECUTIVE_LLM_FAILURES} times in a row.")
                                raise ConfigurationError(abort_msg)
                        else:
                            consecutive_llm_failures = 0

                files_since_gc += 1
                if files_since_gc >= 100:
                    gc.collect()
                    files_since_gc = 0

            except SQLAlchemyError as db_error:
                critical_db_msg = f"Fatal DB Transaction Error: {db_error}"
                self.logger.log_critical_error("DatabaseController", critical_db_msg)
                raise RuntimeError(critical_db_msg) from db_error

            except ConfigurationError:
                raise

            except Exception as loop_crash:
                self.logger.log_critical_error(
                    "BatchOrchestrator",
                    f"Unexpected runtime exception on {input_path.name}: {loop_crash}")

                if file_completed_in_db:
                    pass
                elif file_started_in_db:
                    crash_summary = FileSummary(
                        total_discovered_pages=0,
                        applied_range_string="",
                        range_status_code=Status.FAILURE.value,
                        final_aggregate_status=Status.FAILURE.value,
                        final_aggregate_comment=f"Fatal Orchestration Exception: {loop_crash}"
                    )
                    try:
                        self.db.handle_file_completed(current_id, crash_summary)
                    except SQLAlchemyError as fallback_db_error:
                        fatal_msg = f"Fatal DB Transaction Error during fallback save: {fallback_db_error}"
                        self.logger.log_critical_error("DatabaseController", fatal_msg)
                        raise RuntimeError(fatal_msg) from fallback_db_error
