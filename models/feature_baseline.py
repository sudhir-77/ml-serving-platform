"""Stdlib HTTP feature lookup. This is the Python baseline, backed by a dict."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from mlserving.dict_store import DictFeatureStore  # noqa: E402

STORE = DictFeatureStore()
LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _write(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/health":
            self._write(200, {"status": "ok"})
            return
        self._write(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._write(400, {"error": "bad json"})
            return
        now_ms = int(time.time() * 1000)
        if path == "/v1/events":
            with LOCK:
                stored = STORE.apply(list(body.get("events", [])), now_ms)
            self._write(200, {"stored": stored})
            return
        if path == "/v1/vector":
            with LOCK:
                features = STORE.vector(int(body["user_id"]), int(body["item_id"]), now_ms)
            self._write(200, {"features": features, "found": True})
            return
        self._write(404, {"error": "not found"})


class QueuedHTTPServer(HTTPServer):
    request_queue_size = 256


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ["PORT"])
    # One thread on purpose: this is the simple dict baseline the Rust server is compared with.
    QueuedHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
