# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import os
import shutil
import subprocess
from pathlib import Path
from flask import Blueprint, jsonify

from config_loader import get_app_data_dir
from config_validator import tech_folder_path
from data_exporter import SQLiteDataExporter
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

@exports_bp.route('/database', methods=['POST'])
def export_database():
    try:
        from config_loader import load_for_ui
        merged_settings, _ = load_for_ui()

        import routes.execution_api as exec_api
        if exec_api.processing_thread and exec_api.processing_thread.is_alive():
            return jsonify({
                "status": "error",
                "message": "A processing run is currently active. Please export after it finishes.",
                "message_key": "err_export_run_active"
            }), 409

        output_folder = str(merged_settings.get("OUTPUT_FOLDER_PATH") or "")
        if not output_folder.strip() or output_folder == ".":
            return jsonify({
                "status": "error",
                "message": "No results are currently available. Analyze the files first.",
                "message_key": "err_export_no_results"
            }), 400

        db_path = tech_folder_path(output_folder) / "application_state.db"

        if not Path(get_safe_path(db_path)).exists():
            return jsonify({
                "status": "error",
                "message": "No database found. Run a batch process first.",
                "message_key": "err_export_no_results"
            }), 404

        export_dir = (Path(output_folder) / "exports").resolve()
        Path(get_safe_path(export_dir)).mkdir(parents=True, exist_ok=True)

        exporter = SQLiteDataExporter(db_path)
        try:
            exporter.export_all_formats(export_dir)
        finally:
            exporter.close()

        xlsx_files = sorted(Path(get_safe_path(export_dir)).glob("database_export_*.xlsx"),
                            key=lambda p: p.stat().st_mtime)
        reveal_target = export_dir / xlsx_files[-1].name if xlsx_files else export_dir
        revealed = _reveal_in_explorer(reveal_target)

        return jsonify({
            "status": "success",
            "message": "Database successfully exported.",
            "path": str(export_dir),
            "revealed": revealed
        }), 200

    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to export database: {e}"}), 500

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
                "message": "Could not open the folder. Copy the path above and open it yourself.",
                "message_key": "err_open_folder_failed"
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
