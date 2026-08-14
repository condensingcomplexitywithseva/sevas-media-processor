# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from collections import deque
from datetime import datetime
import queue
from schemas import Status, PageResult, FileSummary
from fs_utils import humanize_paths
from config_loader import get_app_data_dir

VALID_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def resolve_log_level(raw_value) -> int:
    try:
        return VALID_LOG_LEVELS.get(str(raw_value or "").strip().upper(), logging.INFO)
    except Exception:
        return logging.INFO


def categorize(logger_name: str) -> str:
    if "config" in logger_name or "Settings" in logger_name:
        return "CONFIG"
    if "LLM" in logger_name or "llm" in logger_name:
        return "AI"
    if "Orchestrator" in logger_name:
        return "CORE"
    if "UI" in logger_name:
        return "UI"
    if "werkzeug" in logger_name:
        return "CORE"
    if logger_name == "root":
        return "SYSTEM"
    return logger_name


class SystemLogFormatter(logging.Formatter):
    def format(self, record):
        record.category = categorize(record.name)
        return super().format(record)


class SystemLogger:

    def __init__(self):
        self.app_logger = logging.getLogger("SevasMediaProcessor")

    def log_file_started(self, unique_file_id: int, relative_file_path: str, detected_extension: str) -> None:
        self.app_logger.info(f"[{unique_file_id}] STARTING: {relative_file_path} (Type: {detected_extension})")

    def log_frame_saved(self, unique_file_id: int, page_result: PageResult) -> None:
        if page_result.success == Status.FAILURE.value:
            self.app_logger.error(f"[{unique_file_id}] Frame {page_result.page_number} FAILED: {page_result.comment}")
        elif page_result.success == Status.SKIPPED.value:
            self.app_logger.debug(f"[{unique_file_id}] Frame {page_result.page_number} SKIPPED: {page_result.comment}")
        else:
            self.app_logger.debug(f"[{unique_file_id}] Frame {page_result.page_number} SAVED: {page_result.output_filename}")

    def log_file_completed(self, unique_file_id: int, file_summary: FileSummary) -> None:
        if file_summary.final_aggregate_status == Status.OK.value:
            self.app_logger.info(f"[{unique_file_id}] COMPLETED SUCCESSFULLY: {file_summary.final_aggregate_comment}")
        else:
            self.app_logger.warning(f"[{unique_file_id}] COMPLETED WITH ISSUES ({file_summary.final_aggregate_status.upper()}): {file_summary.final_aggregate_comment}")

    def log_llm_completed(self, unique_file_id: int, status_override: str, llm_error: str) -> None:
        if status_override == Status.LLM_FAILED.value:
            self.app_logger.error(f"[{unique_file_id}] AI NETWORK FATAL ERROR: {llm_error}")
        elif status_override == Status.LLM_PARTIAL.value:
            self.app_logger.warning(f"[{unique_file_id}] AI NETWORK PARTIAL SUCCESS: {llm_error}")
        else:
            self.app_logger.info(f"[{unique_file_id}] AI Analysis Completed Successfully.")

    def log_critical_error(self, module_name: str, error_message: str) -> None:
        self.app_logger.critical(f"CRITICAL SYSTEM CRASH [{module_name}]: {error_message}")


system_logger = SystemLogger()


class SSEBroadcaster:
    def __init__(self):
        self.listeners = []
        self.history = deque(maxlen=500)
        self._lock = threading.RLock()

    def add_listener(self, q):
        with self._lock:
            self.listeners.append(q)
            for msg_data in self.history:
                q.put(msg_data)

    def remove_listener(self, q):
        with self._lock:
            if q in self.listeners:
                self.listeners.remove(q)

    def emit(self, event):
        with self._lock:
            if event.get("type") == "log":
                self.history.append(event)
            listeners_copy = list(self.listeners)
        for q in listeners_copy:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


global_broadcaster = SSEBroadcaster()


class GlobalSSEHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            msg = humanize_paths(self.format(record))

            msg_data = {
                "type": "log",
                "category": categorize(record.name),
                "level": record.levelname,
                "timestamp": datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "logger": record.name,
                "content": msg
            }
            client_id = getattr(record, "ui_client_id", None)
            if client_id:
                msg_data["ui_client_id"] = client_id
            global_broadcaster.emit(msg_data)
        except Exception:
            pass


global_sse_handler = GlobalSSEHandler()

_init_lock = threading.Lock()
_configured = False
_active_file_handler = None


def setup_logging(log_level, memory_buffer_handler=None) -> None:
    global _configured, _active_file_handler
    with _init_lock:
        if _configured:
            return
        _configured = True

        root = logging.getLogger()
        root.setLevel(resolve_log_level(log_level))

        logs_dir = get_app_data_dir() / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        try:
            txt_files = sorted(logs_dir.glob("system_log_*.txt*"), key=lambda p: p.stat().st_mtime)
            if len(txt_files) > 30:
                for file_to_delete in txt_files[:-30]:
                    try:
                        if file_to_delete.is_file():
                            file_to_delete.unlink()
                    except OSError:
                        pass
        except Exception:
            pass

        boot_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file_path = logs_dir / f"system_log_{boot_timestamp}.txt"

        file_formatter = SystemLogFormatter("%(asctime)s - [%(category)s] - %(levelname)s - %(message)s")
        _active_file_handler = None
        try:
            file_handler = RotatingFileHandler(log_file_path, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8")
            file_handler.setFormatter(file_formatter)
            _active_file_handler = file_handler

            with open(log_file_path, "a", encoding="utf-8") as f:
                f.write(f"\n=== APPLICATION LAUNCHED AT {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")

        except OSError as file_lock_error:
            logging.getLogger("SevasMediaProcessor").critical(f"Failed to setup persistent logging in AppData. Running console-only. Error: {file_lock_error}")

        if memory_buffer_handler:
            for record in list(memory_buffer_handler.buffer):
                try:
                    global_sse_handler.handle(record)
                except Exception:
                    pass

            if _active_file_handler:
                memory_buffer_handler.setTarget(_active_file_handler)
                memory_buffer_handler.flush()
            root.removeHandler(memory_buffer_handler)

        for handler in list(root.handlers):
            root.removeHandler(handler)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter("%(asctime)s - [%(name)s] - %(levelname)s - %(message)s"))
        root.addHandler(console_handler)
        if _active_file_handler:
            root.addHandler(_active_file_handler)
        root.addHandler(global_sse_handler)

        logging.getLogger("PIL").setLevel(logging.INFO)
        logging.getLogger("av").setLevel(logging.INFO)
        logging.getLogger("urllib3").setLevel(logging.INFO)
        logging.getLogger("werkzeug").setLevel(logging.ERROR)

        if _active_file_handler:
            logging.getLogger("SYSTEM").info(f"Persistent logging established at: {log_file_path}")


def get_active_log_file():
    if _active_file_handler:
        return Path(_active_file_handler.baseFilename).resolve()
    return None


def close_logging() -> None:
    global _active_file_handler
    if _active_file_handler:
        try:
            _active_file_handler.close()
        except Exception:
            pass
        logging.getLogger().removeHandler(_active_file_handler)
        _active_file_handler = None
