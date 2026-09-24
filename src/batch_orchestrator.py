# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import gc
import sys
from sqlalchemy.exc import SQLAlchemyError
from schemas import RequestStatus, Status, FileSummary, ConfigurationError, SourceFingerprint
from pathlib import Path

from fs_utils import get_safe_path, humanize_paths, replace_lone_surrogates, source_sha256, source_state
from media_classifier import SUPPORTED_EXTENSIONS

SOURCE_CHANGED_DURING_CONVERSION = "The source file changed while it was being converted."

LAST_ERROR_CHARS = 300


def last_server_error_note(outcomes) -> str:
    errors = [outcome.error for outcome in outcomes
              if outcome.status in (RequestStatus.NETWORK_FAILURE.value,
                                    RequestStatus.PROVIDER_REPLY_PARSE_ERROR.value)
              and outcome.error]
    if not errors:
        return ""
    text = " ".join(errors[-1].split())
    if len(text) > LAST_ERROR_CHARS:
        text = text[:LAST_ERROR_CHARS] + "..."
    return f" Last error: {text}"


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
        return sorted(self.input_folder / p.relative_to(safe_root)
                      for p in safe_root.rglob("*") if p.is_file())

    def _process_file(self, file_id, input_path, abort_flag, source=None):
        rel_path, ext, pipeline_name, generator, is_orphaned = self.router.evaluate_and_route(
            file_id, input_path, self.input_folder)
        self.db.handle_file_started(file_id, rel_path, ext, pipeline_name,
                                    ai_enabled=self.settings.ENABLE_LLM_INFERENCE, source=source)
        self.logger.log_file_started(file_id, rel_path, ext)
        extraction_recorded = False
        try:
            while True:
                if abort_flag is not None and abort_flag.is_set():
                    summary = FileSummary(0, "", Status.FAILURE.value,
                                          "Aborted mid-extraction by user command.")
                    break
                try:
                    page = next(generator)
                except StopIteration as finished:
                    summary = finished.value
                    break
                self.db.handle_frame_saved(file_id, page)
                self.logger.log_frame_saved(file_id, page)
            if summary is None:
                raise ValueError("Extraction ended without a file summary")
            if is_orphaned:
                summary.file_to_jpegs_comment += humanize_paths(
                    f" [Orphaned path fallback. Original location: {input_path.absolute()}]")
            self.db.handle_file_completed(file_id, summary)
            extraction_recorded = True
            statuses = self.db.get_file_statuses(file_id)
            self.logger.log_file_completed(file_id, statuses.get("file_to_jpegs_status", ""),
                                           summary.file_to_jpegs_comment)
            if source is not None and source_state(input_path) != (source.size, source.mtime_ns):
                self.db.record_file_interruption(file_id, SOURCE_CHANGED_DURING_CONVERSION)
                return []
            if abort_flag is not None and abort_flag.is_set():
                self.db.record_file_interruption(file_id, "Stopped before AI processing completed.")
                return []

            outcomes = []
            expected_requests = 0
            if self.settings.ENABLE_LLM_INFERENCE and self.llm_client is not None:
                frames = self.db.get_successful_frames(file_id, self.router.output_folder)
                if abort_flag is not None and abort_flag.is_set():
                    self.db.record_file_interruption(file_id, "Stopped before AI preparation.")
                    return []
                if frames:
                    expected_requests = (len(frames) + self.settings.MAX_JPEGS_PER_INFERENCE - 1
                                         ) // self.settings.MAX_JPEGS_PER_INFERENCE
                    outcomes = self.llm_client.execute_network_inference(
                        frames, abort_flag=abort_flag,
                        on_outcome=lambda outcome: self.db.handle_llm_requests(file_id, [outcome]))
                    self.logger.log_llm_completed(file_id, outcomes)
            if (abort_flag is not None and abort_flag.is_set()) or any(
                    outcome.status in (RequestStatus.ABORTED_BY_USER.value, RequestStatus.NOT_ATTEMPTED.value)
                    for outcome in outcomes):
                self.db.record_file_interruption(file_id, "Stopped before this file completed.")
            else:
                self.db.finalize_file(file_id, expected_requests)
            return outcomes
        except SQLAlchemyError:
            raise
        except ConfigurationError as error:
            self.db.record_file_interruption(file_id, str(error))
            raise
        except Exception as error:
            if not extraction_recorded:
                self.db.handle_file_completed(file_id, FileSummary(
                    0, "", Status.FAILURE.value, f"Fatal Orchestration Exception: {error}"))
            self.db.record_file_interruption(file_id, f"Processing interrupted: {error}")
            raise
        finally:
            close = getattr(generator, "close", None)
            if close is not None:
                primary_error = sys.exception()
                try:
                    close()
                except Exception as cleanup_error:
                    if primary_error is None:
                        raise
                    self.logger.log_critical_error(
                        "BatchOrchestrator", f"Decoder cleanup also failed for file {file_id}: {cleanup_error}")

    @staticmethod
    def _source_decision(input_path, recorded, abort_flag):
        state = source_state(input_path)
        if state is None:
            return False, None, False
        digest = None
        if recorded:
            if any((fingerprint.size, fingerprint.mtime_ns) == state for fingerprint in recorded):
                return True, None, False
            same_size = {fingerprint.sha256 for fingerprint in recorded
                         if fingerprint.size == state[0] and fingerprint.sha256}
            if same_size:
                digest = source_sha256(input_path, abort_flag)
                if digest is None:
                    return False, None, True
                if digest in same_size:
                    return True, None, False
        if digest is None:
            if Path(input_path).suffix.lower() not in SUPPORTED_EXTENSIONS:
                digest = ""
            else:
                digest = source_sha256(input_path, abort_flag)
                if digest is None:
                    return False, None, True
        return False, SourceFingerprint(state[0], state[1], digest), False

    def execute_batch_processing_loop(self, abort_flag=None, on_progress=None) -> None:
        current_id = self.db.get_highest_file_id()
        completed = self.db.get_no_retry_sources(
            [status.value for status in self.settings.NO_RETRY_STATUSES])
        if abort_flag is not None and abort_flag.is_set():
            return
        files = self._discover_files()
        self.logger.app_logger.info(
            f"Process starting. Next available ID: {current_id + 1}. "
            f"{len(completed)} completed paths are skipped while their source files are unchanged.")
        self.logger.app_logger.info(f"Filesystem scan complete. Total files discovered: {len(files)}")
        if not files:
            self.logger.app_logger.warning("Input folder is empty. Ending process.")
            if on_progress:
                on_progress({"type": "progress", "value": 100})
            return
        network_failures = content_failures = 0
        for index, input_path in enumerate(files):
            if abort_flag is not None and abort_flag.is_set():
                break
            if on_progress:
                on_progress({"type": "progress", "value": int(index / len(files) * 100)})
            rel_path, _ = self.router.relative_or_orphan(input_path, self.input_folder)
            recorded = completed.get(replace_lone_surrogates(rel_path))
            skip, source, stopped = self._source_decision(input_path, recorded, abort_flag)
            if stopped:
                break
            if skip:
                continue
            if recorded is not None:
                self.logger.app_logger.info(
                    f"Processing {rel_path} again: the file differs from its completed attempt "
                    "or could not be checked.")
            current_id += 1
            try:
                outcomes = self._process_file(current_id, input_path, abort_flag, source)
            except SQLAlchemyError as error:
                message = f"Fatal DB Transaction Error: {error}"
                self.logger.log_critical_error("DatabaseController", message)
                raise RuntimeError(message) from error
            except ConfigurationError:
                raise
            except Exception as error:
                self.logger.log_critical_error(
                    "BatchOrchestrator", f"Unexpected runtime exception on {input_path.name}: {error}")
                continue

            statuses = [outcome.status for outcome in outcomes]
            if RequestStatus.OK.value in statuses:
                network_failures = content_failures = 0
            elif any(status in (RequestStatus.NETWORK_FAILURE.value,
                                RequestStatus.PROVIDER_REPLY_PARSE_ERROR.value) for status in statuses):
                network_failures += 1
                if network_failures >= self.settings.MAX_CONSECUTIVE_LLM_FAILURES:
                    raise ConfigurationError(
                        "CIRCUIT BREAKER TRIPPED: the AI server failed on "
                        f"{self.settings.MAX_CONSECUTIVE_LLM_FAILURES} files with no successful reply in between."
                        f"{last_server_error_note(outcomes)}")
            elif RequestStatus.INVALID_JSON_ANSWER.value in statuses:
                content_failures += 1
                if content_failures >= self.settings.MAX_CONSECUTIVE_LLM_FAILURES:
                    raise ConfigurationError(
                        "CIRCUIT BREAKER TRIPPED: the AI failed to produce valid JSON on "
                        f"{self.settings.MAX_CONSECUTIVE_LLM_FAILURES} files in a row.")
            if (index + 1) % 100 == 0:
                gc.collect()
