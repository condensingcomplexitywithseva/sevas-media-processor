# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

import json
import os
import re
import subprocess
import logging
from functools import wraps
from typing import Any
from pathlib import Path
from flask import Blueprint, request, jsonify, make_response
from werkzeug.exceptions import HTTPException
from config_loader import (Settings, ROOT_DIR, get_masked_env_tokens, get_settings_path,
                           update_env_tokens, load_for_ui, validate_draft, save_settings,
                           log_settings_errors, real_token_updates, is_broken_file)
from config_loader import settings_with_edits, settings_transaction, settings_operations
from config_validator import settings_form_view, settings_form_repair_fields
from fs_utils import get_safe_path, text_looks_binary

api_blueprint = Blueprint('api', __name__)
logger = logging.getLogger(__name__)


def settings_snapshot():
    operations = settings_operations()
    merged, errors = load_for_ui()
    return {
        "epoch": operations.epoch, "revision": operations.revision,
        "settings": settings_form_view(merged),
        "repair_fields": settings_form_repair_fields(merged),
        "env_tokens": get_masked_env_tokens(), "errors": errors,
        "token_wipes": dict(operations.token_wipes),
        "reset_revision": operations.reset_revision, "backup_path": operations.backup_path,
    }


def settings_operation(function):
    @wraps(function)
    def operation():
        operations = settings_operations()
        identity = request.headers.get("X-Settings-Operation", "")
        base = operations.revision
        digest = ""
        if identity:
            epoch = request.headers.get("X-Settings-Epoch", "")
            revision = request.headers.get("X-Settings-Revision", "")
            if (epoch != operations.epoch or not re.fullmatch(r"[a-f0-9-]{36}", identity)
                    or not revision.isdecimal() or int(revision) > operations.revision):
                return jsonify(status="fatal", message_key="err_settings_operation_unknown"), 409
            base = int(revision)
            digest = operations.fingerprint(request.path, request.get_data())
            previous = operations.receipts.get(identity)
            if previous:
                original_digest, result, code, _ = previous
                if original_digest != digest:
                    return jsonify(status="fatal", message_key="err_settings_operation_unknown"), 409
                return jsonify(dict(result, snapshot=settings_snapshot())), code
            if base <= operations.expired_through:
                return jsonify(status="fatal", message_key="err_settings_operation_unknown"), 409

        if identity and function.__name__ == "commit_settings" and base != operations.revision:
            result: dict[str, Any] = {"status": "superseded", "message_key": "err_settings_changed_retry"}
            code = 409
        else:
            try:
                response = make_response(function())
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception("Settings operation failed")
                response = make_response(jsonify(status="fatal", message=str(exc)), 500)
            result = response.get_json()
            code = response.status_code

        operations.revision += 1
        if function.__name__ == "wipe_token" and result.get("status") == "success":
            operations.token_wipes[request.get_json()["provider"]] = operations.revision
        if function.__name__ == "reset_settings":
            operations.reset_revision = operations.revision
            if result.get("status") == "success":
                operations.backup_path = result.get("backup_path", "")
        if function.__name__ == "commit_settings" and result.get("status") == "success":
            operations.backup_path = ""
        result["operation_revision"] = operations.revision
        if identity:
            operations.remember(identity, digest, result, code, base)
        return jsonify(dict(result, snapshot=settings_snapshot())), code
    return operation


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
@settings_transaction()
@settings_operation
def commit_settings():
    body = request.get_json()
    if not isinstance(body, dict):
        return jsonify({"status": "error", "errors": {
            "general": {"type": "i18n", "value": "err_settings_invalid"}}}), 400
    logger.info("Synchronizing configuration changes with disk storage...")

    _, current_errors = load_for_ui()
    if is_broken_file(current_errors):
        logger.warning("Synchronization ABORTED: The settings.json file on disk "
                       "is corrupted. Cannot safely commit UI changes.")
        return jsonify({"status": "error", "errors": current_errors}), 400

    try:
        if set(body) == {"edits"}:
            try:
                payload = settings_with_edits(body["edits"])
            except (ValueError, TypeError, AttributeError):
                return jsonify({"status": "error", "errors": {
                    "general": {"type": "i18n", "value": "err_settings_invalid"}}}), 400
        else:
            payload = unflatten_dict(body)

        logger.info("Running pre-commit validation suite...")
        settings_obj, errors, merged = validate_draft(payload)

        if errors:
            log_settings_errors(errors)
            logger.warning(f"Synchronization ABORTED: {len(errors)} validation errors detected.")
            return jsonify({"status": "error", "settings": settings_form_view(merged), "errors": errors}), 400

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
            "settings": settings_form_view(merged),
            "repair_fields": settings_form_repair_fields(merged),
            "errors": fresh_errors,
            "env_tokens": get_masked_env_tokens()
        }), 200

    except Exception as e:
        logger.error(f"Fatal error in commit_settings: {e}", exc_info=True)
        return jsonify({"status": "fatal", "message": str(e)}), 500

@api_blueprint.route('/settings/wipe_token', methods=['POST'])
@settings_transaction()
@settings_operation
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
                "message": "There is no settings backup to open. Reset cannot recover an earlier missing file.",
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
@settings_transaction()
@settings_operation
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
