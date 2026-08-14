# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from flask import Flask, Response, request
from werkzeug.serving import make_server



@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body_bytes: bytes
    json: Any
    json_ok: bool

    @property
    def text(self) -> str:
        return self.body_bytes.decode("utf-8", errors="replace")




class ProviderServer:

    name: str = "provider"
    default_port: int = 0

    STALL_SAFETY_SECONDS = 20

    def __init__(self) -> None:
        self.requests: list[RecordedRequest] = []
        self._script: list[dict] = []
        self._lock = threading.Lock()
        self._stall_release = threading.Event()

        self._app = Flask(f"fake-{self.name}")
        self.register_routes(self._app)
        self._install_fallback(self._app)

        self._server = None
        self._thread = None


    def register_routes(self, app: Flask) -> None:
        raise NotImplementedError

    def handle_unknown_route(self):
        return self.json_response({"error": "not found"}, status=404)


    def start(self, port: Optional[int] = None, host: str = "127.0.0.1"):
        bind_port = 0 if port is None else port
        self._server = make_server(host, bind_port, self._app, threaded=True)
        self._host = host
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name=f"fake-{self.name}"
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stall_release.set()
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def port(self) -> int:
        return self._server.server_port

    @property
    def base_url(self) -> str:
        return f"http://{self._host}:{self.port}"


    def _record(self) -> RecordedRequest:
        body = request.get_data(cache=True)
        parsed: Any = None
        ok = False
        if body:
            try:
                parsed = json.loads(body)
                ok = True
            except (ValueError, TypeError):
                parsed, ok = None, False
        rec = RecordedRequest(
            method=request.method,
            path=request.path,
            query={k: v for k, v in request.args.items()},
            headers={k: v for k, v in request.headers.items()},
            body_bytes=bytes(body),
            json=parsed,
            json_ok=ok,
        )
        with self._lock:
            self.requests.append(rec)
        return rec

    def intercept(self):
        rec = self._record()
        with self._lock:
            behavior = self._script.pop(0) if self._script else None
        if behavior is None:
            return rec, None
        return rec, self._apply(behavior, rec)

    def _apply(self, behavior: dict, rec: RecordedRequest) -> Response:
        hook = behavior.get("on_request")
        if hook is not None:
            hook(rec)

        if behavior.get("stall"):
            self._stall_release.wait(
                behavior.get("stall_seconds", self.STALL_SAFETY_SECONDS)
            )
            return self.json_response({"released": "stall ended"}, status=200)

        status = behavior.get("status", 200)
        extra = behavior.get("headers")

        if "truncate_body_after" in behavior:
            full = json.dumps(behavior.get("json", {}),
                              ensure_ascii=False).encode("utf-8")
            partial = full[: behavior["truncate_body_after"]]

            def generate_partial():
                yield partial

            resp = Response(generate_partial(), status=status,
                            mimetype="application/json")
            resp.headers["Content-Length"] = str(len(full))
            if extra:
                resp.headers.update(extra)
            return resp

        if "sse" in behavior:
            return self.sse_response(behavior["sse"], status=status, headers=extra)
        if "ndjson" in behavior:
            return self.ndjson_response(behavior["ndjson"], status=status, headers=extra)
        if "raw" in behavior:
            ct = behavior.get("content_type", "text/html")
            resp = Response(behavior["raw"], status=status)
            resp.headers["Content-Type"] = ct
            if extra:
                resp.headers.update(extra)
            return resp
        return self.json_response(behavior.get("json", {}), status=status, headers=extra)

    def queue(self, **behavior) -> "ProviderServer":
        with self._lock:
            self._script.append(behavior)
        return self


    def json_response(self, obj: Any, status: int = 200,
                      headers: Optional[dict] = None) -> Response:
        body = json.dumps(obj, ensure_ascii=False)
        resp = Response(body, status=status)
        resp.headers["Content-Type"] = "application/json"
        self._decorate(resp)
        if headers:
            resp.headers.update(headers)
        return resp

    def sse_response(self, events: Iterable[str], status: int = 200,
                     headers: Optional[dict] = None) -> Response:
        def generate():
            for ev in events:
                text = ev if ev.endswith("\n\n") else ev + "\n\n"
                yield text

        resp = Response(generate(), status=status, mimetype="text/event-stream")
        resp.headers["Cache-Control"] = "no-cache"
        self._decorate(resp)
        if headers:
            resp.headers.update(headers)
        return resp

    def ndjson_response(self, lines: Iterable[Any], status: int = 200,
                        headers: Optional[dict] = None) -> Response:
        def generate():
            for obj in lines:
                yield (obj if isinstance(obj, str)
                       else json.dumps(obj, ensure_ascii=False)) + "\n"

        resp = Response(generate(), status=status,
                        mimetype="application/x-ndjson")
        self._decorate(resp)
        if headers:
            resp.headers.update(headers)
        return resp

    def _decorate(self, resp: Response) -> None:
        return None


    def _install_fallback(self, app: Flask) -> None:
        app.add_url_rule(
            "/", "._fallback_root", self._fallback,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        )
        app.add_url_rule(
            "/<path:_any>", "._fallback", self._fallback,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        )

    def _fallback(self, _any: str = ""):
        rec, scripted = self.intercept()
        if scripted is not None:
            return scripted
        return self.handle_unknown_route()



@dataclass
class RunningProvider:
    name: str
    server: ProviderServer
    base_url: str


class MultiServer:

    def __init__(self, servers: list[ProviderServer]):
        self._servers = servers
        self.running: list[RunningProvider] = []

    def start(self, host: str = "127.0.0.1",
              ports: Optional[dict[str, int]] = None) -> "MultiServer":
        for srv in self._servers:
            port = (ports or {}).get(srv.name, srv.default_port)
            srv.start(port=port, host=host)
            self.running.append(RunningProvider(srv.name, srv, srv.base_url))
        return self

    def stop(self) -> None:
        for rp in self.running:
            rp.server.stop()
        self.running = []
