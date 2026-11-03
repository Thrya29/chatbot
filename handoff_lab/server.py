"""The bridge as a real HTTP service (standard library only, so it runs anywhere).

    python -m handoff_lab.server --port 8080

Endpoints:
  POST /webhooks/assistant   signed events from the AI assistant platform (X-Signature-256)
  POST /webhooks/genesys     signed Open Messaging outbound events (X-Hub-Signature-256)
  GET  /healthz              liveness
  GET  /metrics              counters (deliveries, duplicates, dead letters)

In this demo the downstream systems are the in-process mocks from lab.Lab, so the
service can be exercised with curl. In production the same Bridge class would be
given real HTTP clients for Genesys and the assistant platform, a real database for
the Store, and would run behind TLS; see docs/architecture.md.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

from .lab import Lab

MAX_BODY = 256 * 1024


def make_server(lab: Lab, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    counters: Counter = Counter()
    lock = Lock()          # the Bridge and SQLite store are used from one thread at a time

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                return self._send(200, {"ok": True})
            if self.path == "/metrics":
                with lock:
                    return self._send(200, {**counters, "dead_letters": len(lab.store.dead_letters()),
                                            "genesys_breaker": lab.bridge.breaker.snapshot()})
            self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "body too large"})
            body = self.rfile.read(length)
            headers = {k: v for k, v in self.headers.items()}
            with lock:
                if self.path == "/webhooks/assistant":
                    code, out = lab.bridge.on_assistant_webhook(body, headers)
                elif self.path == "/webhooks/genesys":
                    code, out = lab.bridge.on_genesys_webhook(body, headers)
                else:
                    return self._send(404, {"error": "not found"})
                counters[f"{self.path}:{code}"] += 1
            self._send(code, out)

        def log_message(self, *args):   # keep test output quiet
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()
    srv = make_server(Lab(), port=args.port)
    print(f"bridge listening on http://127.0.0.1:{args.port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
