# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0


import threading
import queue
import logging
import json
import uuid
from schemas import exception_message
from flask import Blueprint, jsonify, Response, request
from app_context import ProcessorCore
from central_logger import global_broadcaster

execution_bp = Blueprint("execution", __name__)


class RunController:

    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.abort = threading.Event()
        self.run_id = ""
        self.revision = 0
        self.phase = "idle"
        self.progress = 0
        self.terminal: dict = {}
        self.export_barrier = 0
        self.export_result: dict | None = None

    @property
    def active(self):
        return self.phase in ("starting", "running", "stopping")

    def snapshot(self):
        return {"run_id": self.run_id, "revision": self.revision, "phase": self.phase,
                "progress": self.progress, "is_running": self.active,
                "is_stopping": self.phase == "stopping", "terminal": self.terminal,
                "export_barrier": self.export_barrier, "export_result": self.export_result}

    def emit(self, run_id, event):
        with self.lock:
            if run_id != self.run_id or not self.active:
                return
            kind = event.get("type")
            if kind in ("done", "aborted", "failed"):
                self.terminal = dict(event)
                return
            if kind == "progress":
                self.progress = event["value"]
            elif kind == "export_result":
                self.export_result = dict(event)
            self.revision += 1
            global_broadcaster.emit({**event, "run_state": self.snapshot()})

    def worker(self, core, run_id, abort):
        try:
            core.run()
        except Exception as error:
            logging.getLogger("Orchestrator.Worker").exception("FATAL ERROR in processing worker")
            with self.lock:
                if run_id == self.run_id:
                    self.terminal = {"type": "failed", **exception_message(error)}
        finally:
            with self.lock:
                if run_id == self.run_id:
                    self.phase = self.terminal.get("type", "aborted" if abort.is_set() else "done")
                    self.terminal = {**self.terminal, "type": self.phase}
                    self.revision += 1
                    global_broadcaster.emit({**self.terminal, "run_state": self.snapshot()})

    def stop(self, run_id):
        with self.lock:
            if run_id != self.run_id or not self.active:
                return False, self.snapshot()
            self.abort.set()
            self.phase = "stopping"
            self.revision += 1
            snapshot = self.snapshot()
            global_broadcaster.emit({"type": "run_state", "run_state": snapshot})
            return True, snapshot


run_controller = RunController()


@execution_bp.route("/start", methods=["POST"])
def start_processing():
    controller = run_controller
    with controller.lock:
        if controller.active:
            return jsonify({"status": "error", "message": "Processing is already running.",
                            "message_key": "err_run_active", "run_state": controller.snapshot()}), 400
        run_id = request.headers.get("X-Run-Id") or str(uuid.uuid4())
        if run_id == controller.run_id:
            return jsonify({"status": "success", "run_state": controller.snapshot(),
                            "export_barrier": controller.export_barrier}), 200
        abort = threading.Event()
        try:
            from config_loader import load_strict
            core = ProcessorCore(load_strict(), abort,
                                 on_progress=lambda event: controller.emit(run_id, event))
        except Exception as error:
            from config_loader import load_for_ui
            _, errors = load_for_ui()
            return jsonify({**exception_message(error), "errors": errors,
                            "run_state": controller.snapshot()}), 400
        from routes.export_api import advance_export_revision
        controller.run_id, controller.abort = run_id, abort
        controller.phase, controller.progress, controller.terminal = "running", 0, {}
        controller.export_result = None
        controller.export_barrier = advance_export_revision()
        controller.revision += 1
        controller.thread = threading.Thread(target=controller.worker, args=(core, run_id, abort), daemon=True)
        controller.thread.start()
        snapshot = controller.snapshot()
        global_broadcaster.emit({"type": "run_state", "run_state": snapshot})
        return jsonify({"status": "success", "message": "Processing started.",
                        "export_barrier": controller.export_barrier, "run_state": snapshot}), 200


@execution_bp.route("/stop", methods=["POST"])
def stop_processing():
    accepted, snapshot = run_controller.stop(request.headers.get("X-Run-Id", ""))
    return jsonify({"status": "success" if accepted else "error", "run_state": snapshot,
                    "message": "Stop signal sent." if accepted else "No matching active run to stop."}), (
                        200 if accepted else 409)


@execution_bp.route("/status", methods=["GET"])
def get_status():
    with run_controller.lock:
        return jsonify(run_controller.snapshot()), 200


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
