# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import threading
import queue
import logging
import json
from flask import Blueprint, jsonify, Response

from app_context import ProcessorCore
from central_logger import global_broadcaster

execution_bp = Blueprint("execution", __name__)

processing_thread = None
abort_flag = threading.Event()

_start_lock = threading.Lock()


def _background_worker(core: ProcessorCore, current_abort_flag: threading.Event):
    try:
        core.run()
    except Exception:
        logging.getLogger("Orchestrator.Worker").exception("FATAL ERROR in processing worker")
        global_broadcaster.emit({"type": "failed"})


@execution_bp.route("/start", methods=["POST"])
def start_processing():
    global processing_thread, abort_flag
    with _start_lock:
        return _start_processing_locked()


def _start_processing_locked():
    global processing_thread, abort_flag
    if processing_thread and processing_thread.is_alive():
        return jsonify({
            "status": "error",
            "message": "Processing is already running.",
            "message_key": "err_run_active"
        }), 400

    try:
        abort_flag.clear()
        from config_loader import load_strict

        core = ProcessorCore(load_strict(), abort_flag, on_progress=global_broadcaster.emit)

    except Exception as e:
        from config_loader import load_for_ui

        _, errors = load_for_ui()
        return jsonify({"status": "error", "message": str(e), "errors": errors}), 400

    processing_thread = threading.Thread(
        target=_background_worker, args=(core, abort_flag), daemon=True
    )
    processing_thread.start()
    return jsonify({"status": "success", "message": "Processing started."}), 200


@execution_bp.route("/stop", methods=["POST"])
def stop_processing():
    global processing_thread, abort_flag
    if not processing_thread or not processing_thread.is_alive():
        return jsonify({"status": "error", "message": "No active process to stop."}), 400
    abort_flag.set()
    return (
        jsonify({"status": "success", "message": "Stop signal sent. Waiting for clean exit."}),
        200,
    )


@execution_bp.route("/status", methods=["GET"])
def get_status():
    global processing_thread, abort_flag
    is_running = processing_thread is not None and processing_thread.is_alive()
    is_stopping = is_running and abort_flag.is_set()
    return jsonify({"is_running": is_running, "is_stopping": is_stopping}), 200


@execution_bp.route("/stream", methods=["GET"])
def stream():
    def event_stream():
        from central_logger import global_broadcaster

        q = queue.Queue(maxsize=1000)
        global_broadcaster.add_listener(q)
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield f"data: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    yield ": keep-alive\n\n"
        except GeneratorExit:
            pass
        except Exception:
            pass
        finally:
            global_broadcaster.remove_listener(q)

    return Response(event_stream(), mimetype="text/event-stream")
