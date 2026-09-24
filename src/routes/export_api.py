# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import os
import secrets
import shutil
import subprocess
from pathlib import Path
from threading import Lock
from typing import Any
from flask import Blueprint, jsonify, request

from config_loader import get_app_data_dir
from config_validator import tech_folder_path
from data_exporter import SQLiteDataExporter, ExportError
from fs_utils import get_safe_path

exports_bp = Blueprint('exports', __name__)


def _explorer_path() -> str:
    return os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"),
                        "explorer.exe")


def _reveal_in_explorer(path) -> bool:
    try:
        subprocess.Popen([_explorer_path(), "/select," + str(path)])
        return True
    except Exception:
        return False


def _open_in_explorer(folder) -> bool:
    try:
        subprocess.Popen([_explorer_path(), str(folder)])
        return True
    except Exception:
        return False

_recovery_sources: dict[str, tuple[Path, tuple[int, int]]] = {}


def remember_export_source(database: Path) -> str:
    info = Path(get_safe_path(database)).stat()
    identity = (info.st_dev, info.st_ino)
    for token, source in _recovery_sources.items():
        if source == (database, identity):
            return token
    token = secrets.token_urlsafe(24)
    _recovery_sources[token] = (database, identity)
    return token


_export_revision = 0
_export_revision_lock = Lock()


def advance_export_revision() -> int:
    global _export_revision
    with _export_revision_lock:
        _export_revision += 1
        return _export_revision


def number_export_result(data: dict[str, Any]) -> dict[str, Any]:
    return dict(data, export_revision=advance_export_revision())


def _export_response(data: dict[str, Any]):
    return jsonify(number_export_result(data))


@exports_bp.route('/database', methods=['POST'])
def export_database():
    import routes.execution_api as exec_api
    if not exec_api.run_controller.lock.acquire(blocking=False):
        return _export_response({"status": "error", "export_state": "refused",
                                "message_key": "err_export_run_active",
                        "message": "Another run or export is active."}), 409
    try:
        if exec_api.run_controller.active:
            return _export_response({"status": "error", "export_state": "refused",
                                "message_key": "err_export_run_active",
                            "message": "A processing run is currently active. Please export after it finishes."}), 409
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return _export_response({"status": "error", "export_state": "refused",
                                "message_key": "err_export_request_invalid",
                            "message": "The export request could not be read. Try exporting again."}), 400
        recovery_id = payload.get("recovery_id")
        if recovery_id:
            source = _recovery_sources.get(recovery_id) if isinstance(recovery_id, str) else None
            if source is None:
                return _export_response({"status": "error", "export_state": "refused",
                                "message_key": "err_export_source_changed",
                                "message": "The saved run is no longer available."}), 409
            db_path, identity = source
            try:
                info = Path(get_safe_path(db_path)).stat()
            except OSError:
                info = None
            if info is None or (info.st_dev, info.st_ino) != identity:
                return _export_response({"status": "error", "export_state": "refused",
                                "message_key": "err_export_source_changed",
                                "message": "The saved run is no longer available."}), 409
            export_dir = db_path.parent / "exports"
        else:
            from config_loader import load_for_ui
            merged_settings, _ = load_for_ui()
            output_folder = str(merged_settings.get("OUTPUT_FOLDER_PATH") or "")
            if not output_folder.strip() or output_folder == ".":
                return _export_response({"status": "error", "export_state": "refused",
                                "message_key": "err_export_no_folder",
                                "message": "Choose an Output folder and apply the change before exporting reports."
                                }), 400
            db_path = tech_folder_path(output_folder) / "application_state.db"
            if not Path(get_safe_path(db_path)).is_file():
                return _export_response({"status": "error", "export_state": "refused",
                                "message_key": "err_export_no_results",
                                "message": "No database found. Run a batch process first."}), 404
            export_dir = Path(output_folder) / "exports"
            recovery_id = remember_export_source(db_path)
        destination = payload.get("destination")
        if destination is not None:
            if not isinstance(destination, str) or not destination.strip():
                return _export_response({"status": "error", "export_state": "refused",
                                "message_key": "err_export_destination_required",
                                "message": "Choose a destination folder before saving reports elsewhere."}), 400
            export_dir = Path(destination)
        exporter = SQLiteDataExporter(db_path)
        try:
            outcome = exporter.export_all_formats(export_dir)
        except ExportError as exc:
            outcome = exc.outcome
        data = outcome.as_dict()
        data["export_state"] = "completed"
        data.pop("database", None)
        data["recovery_id"] = recovery_id
        targets = [Path(r["path"]) for r in outcome.saved if r["format"] == "xlsx"]
        data["revealed"] = _reveal_in_explorer(targets[-1]) if targets else False
        data["message_key"] = "export_partial" if outcome.failed else "export_complete"
        return _export_response(data), 207 if outcome.failed else 200
    except Exception as exc:
        return _export_response({"status": "error", "export_state": "unknown", "detail": str(exc),
                        "message": "The export result could not be confirmed. Check the destination folder.",
                        "message_key": "err_export_outcome_unknown",
                        "path": str(locals().get("export_dir", "")),
                        "recovery_id": locals().get("recovery_id")}), 500
    finally:
        exec_api.run_controller.lock.release()


@exports_bp.route('/logs', methods=['POST'])
def export_logs():
    try:
        from config_loader import load_for_ui
        merged_settings, _ = load_for_ui()

        output_folder = str(merged_settings.get("OUTPUT_FOLDER_PATH") or "")
        if not output_folder.strip() or output_folder == ".":
            return jsonify({
                "status": "error",
                "message": "No Output folder is set. Select one and apply it first.",
                "message_key": "err_export_logs_no_folder"
            }), 400

        logs_dir = get_app_data_dir() / "logs"
        if not logs_dir.exists():
            return jsonify({
                "status": "error",
                "message": "No system log found.",
                "message_key": "err_export_no_log"
            }), 404

        txt_files = sorted(logs_dir.glob("system_log_*.txt"), key=lambda p: p.stat().st_mtime)
        if not txt_files:
            return jsonify({
                "status": "error",
                "message": "No system log found.",
                "message_key": "err_export_no_log"
            }), 404

        active_log_path = txt_files[-1]

        export_dir = (Path(output_folder) / "exports").resolve()
        Path(get_safe_path(export_dir)).mkdir(parents=True, exist_ok=True)

        dest_path = export_dir / active_log_path.name
        shutil.copy2(active_log_path, get_safe_path(dest_path))

        revealed = _reveal_in_explorer(dest_path)

        return jsonify({
            "status": "success",
            "message": "System log exported.",
            "path": str(dest_path),
            "revealed": revealed
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@exports_bp.route('/open_logs_folder', methods=['POST'])
def open_logs_folder():
    try:
        logs_dir = get_app_data_dir() / "logs"
        if not logs_dir.exists():
            return jsonify({
                "status": "error",
                "message": "No system log found.",
                "message_key": "err_export_no_log"
            }), 404

        if not _open_in_explorer(logs_dir):
            return jsonify({
                "status": "error",
                "message": "Could not open the folder. Copy this path and paste it into File Explorer's address bar.",
                "message_key": "err_open_folder_failed",
                "path": str(logs_dir)
            }), 500

        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@exports_bp.route('/clear_logs', methods=['POST'])
def clear_logs():
    try:
        active_log_path = None
        try:
            from central_logger import get_active_log_file
            active_log_path = get_active_log_file()
        except Exception:
            active_log_path = None

        logs_dir = get_app_data_dir() / "logs"
        if logs_dir.exists():
            for txt_file in logs_dir.glob("system_log_*.txt*"):
                try:
                    if active_log_path is not None and txt_file.resolve() == active_log_path:
                        continue
                    if txt_file.is_file():
                        txt_file.unlink()
                except OSError:
                    pass
        return jsonify({
            "status": "success",
            "message": "Historical logs cleared successfully."
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
