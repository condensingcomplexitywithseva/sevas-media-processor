# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import subprocess
import logging
from pathlib import Path
from flask import Blueprint, request, jsonify
from config_loader import (Settings, ROOT_DIR, get_masked_env_tokens, get_settings_path,
                           update_env_tokens, load_for_ui, validate_draft, save_settings,
                           log_settings_errors, real_token_updates, is_broken_file)
from fs_utils import get_safe_path, text_looks_binary

api_blueprint = Blueprint('api', __name__)
logger = logging.getLogger(__name__)


def unflatten_dict(flat_dict: dict) -> dict:
    result = {}
    for key, value in flat_dict.items():
        if not isinstance(key, str) or "." not in key:
            result[key] = value
            continue
        parts = key.split(".")
        target = result
        for part in parts[:-1]:
            if part not in target or not isinstance(target[part], dict):
                target[part] = {}
            target = target[part]
        target[parts[-1]] = value
    return result

@api_blueprint.route('/settings/commit', methods=['POST'])
def commit_settings():
    logger.info("Synchronizing configuration changes with disk storage...")

    _, current_errors = load_for_ui()
    if is_broken_file(current_errors):
        logger.warning("Synchronization ABORTED: The settings.json file on disk "
                       "is corrupted. Cannot safely commit UI changes.")
        return jsonify({"status": "error", "errors": current_errors}), 400

    payload = unflatten_dict(request.json)
    try:
        logger.info("Running pre-commit validation suite...")
        settings_obj, errors, merged = validate_draft(payload)

        if errors:
            log_settings_errors(errors)
            logger.warning(f"Synchronization ABORTED: {len(errors)} validation errors detected.")
            return jsonify({"status": "error", "settings": merged, "errors": errors}), 400

        logger.info("Validation SUCCESS. Committing changes to settings.json.")

        assert settings_obj is not None
        save_settings(settings_obj)

        env_updates = real_token_updates(payload.get("ENV_TOKENS"))
        if env_updates:
            update_env_tokens(env_updates)

        merged, fresh_errors = load_for_ui()

        logger.info("Synchronization complete: Disk state verified and synchronized.")

        return jsonify({
            "status": "success",
            "settings": merged,
            "errors": fresh_errors,
            "env_tokens": get_masked_env_tokens()
        }), 200

    except Exception as e:
        logger.error(f"Fatal error in commit_settings: {e}", exc_info=True)
        return jsonify({"status": "fatal", "message": str(e)}), 500

@api_blueprint.route('/settings/wipe_token', methods=['POST'])
def wipe_token():
    data = request.json or {}
    provider = data.get("provider")
    if not provider:
        return jsonify({"status": "error", "message": "No provider specified"}), 400

    logger.info(f"Wiping token for provider: {provider}")
    update_env_tokens({provider: ""})

    _merged, errors = load_for_ui()
    if errors:
        log_settings_errors(errors)

    return jsonify({
        "status": "success",
        "errors": errors,
        "env_tokens": get_masked_env_tokens()
    }), 200


def _notepad_path() -> str:
    return os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"),
                        "System32", "notepad.exe")


@api_blueprint.route('/settings/open_file', methods=['POST'])
def open_settings_file():
    data = request.json or {}
    target = data.get("target", "active")
    active_path = get_settings_path()

    if target == "backup":
        backups = list(active_path.parent.glob("settings_corrupted_backup_*.json"))
        if not backups:
            return jsonify({
                "status": "error",
                "message_key": "err_no_settings_backup",
                "path": str(active_path.parent),
                "message": f"No corrupted-settings backup was found in {active_path.parent}",
            }), 404
        target_path = sorted(backups)[-1]
    else:
        target_path = active_path

    if not target_path.exists():
        return jsonify({
            "status": "error",
            "message_key": "err_settings_file_missing",
            "path": str(target_path),
            "message": f"The file is no longer on disk: {target_path}",
        }), 404

    try:
        subprocess.Popen([_notepad_path(), str(target_path)])
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Failed to open {target_path}: {e}")
        return jsonify({
            "status": "error",
            "message_key": "err_editor_launch_failed",
            "path": str(target_path),
            "message": f"Could not start the text editor. Open this file yourself: {target_path}",
        }), 500

@api_blueprint.route('/settings/reset', methods=['POST'])
def reset_settings():
    try:
        active_path = get_settings_path()
        timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = active_path.parent / f"settings_corrupted_backup_{timestamp}.json"

        if active_path.exists():
            os.replace(active_path, backup_path)

        save_settings(Settings())

        return jsonify({"status": "success", "backup_path": str(backup_path)}), 200
    except Exception as e:
        logger.error(f"Failed to reset settings: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@api_blueprint.route('/locales/<lang>.json', methods=['GET'])
def get_locale(lang):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", lang or ""):
        return jsonify({"error": "Locale not found"}), 404

    locales_dir = (ROOT_DIR / "src" / "locales").resolve()
    file_path = (locales_dir / f"{lang}.json").resolve()

    if not file_path.is_relative_to(locales_dir):
        return jsonify({"error": "Locale not found"}), 404

    if not file_path.exists():
        return jsonify({"error": "Locale not found"}), 404

    try:
        with open(file_path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api_blueprint.route('/preview/file', methods=['POST'])
def preview_file():
    data = request.json
    filepath = data.get("filepath", "")

    if not filepath or not filepath.lower().endswith(".txt"):
        key = "preview_not_txt" if filepath else "preview_no_file"
        return jsonify({"preview_type": "error", "content": key}), 400

    try:
        safe_path = get_safe_path(Path(filepath))
    except (OSError, ValueError):
        return jsonify({"preview_type": "error", "content": "preview_no_file"}), 400
    if not os.path.isfile(safe_path):
        return jsonify({"preview_type": "error", "content": "preview_no_file"}), 400

    try:
        lines = []
        with open(safe_path, encoding="utf-8") as f:
            for _ in range(10):
                line = f.readline(8192)
                if not line:
                    break
                lines.append(line)
        content = "".join(lines).strip()
        if text_looks_binary(content):
            return jsonify({"preview_type": "error", "content": "preview_read_failed"}), 500
        type_str = "full" if len(lines) < 10 else "top10"
        return jsonify({"preview_type": type_str, "content": content}), 200
    except Exception:
        return jsonify({"preview_type": "error", "content": "preview_read_failed"}), 500

@api_blueprint.route('/log', methods=['POST'])
def receive_ui_log():
    data = request.json or {}
    content = str(data.get("content", ""))
    client_id = str(data.get("client_id", ""))[:64]

    ALLOWED_CATEGORIES = {"UI", "CONFIG", "CORE", "AI", "SYSTEM"}
    category = str(data.get("category", "UI")).upper()
    if category not in ALLOWED_CATEGORIES:
        category = "UI"

    from central_logger import resolve_log_level
    level = resolve_log_level(data.get("level", "INFO"))

    extra = {"ui_client_id": client_id} if client_id else None
    logging.getLogger(category).log(level, content, extra=extra)

    return jsonify({"status": "success"}), 200
