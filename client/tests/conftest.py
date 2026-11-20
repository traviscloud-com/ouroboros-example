import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _CaptureState:
    def __init__(self):
        self.requests: list[dict] = []  # {body, headers}
        self.status = 202
        self.delay = 0.0
        self.responder = None  # optional callable(body_dict) -> int status


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        state: _CaptureState = self.server.state  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        state.requests.append({"path": self.path, "body": body, "headers": dict(self.headers)})
        if state.delay:
            time.sleep(state.delay)
        status = state.responder(body) if state.responder else state.status
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


class CaptureServer:
    def __init__(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.state = _CaptureState()  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def state(self) -> _CaptureState:
        return self._server.state  # type: ignore[attr-defined]

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def events(self) -> list[dict]:
        return [e for r in self.state.requests for e in r["body"].get("events", [])]

    def requests_to(self, path: str) -> list[dict]:
        return [r for r in self.state.requests if r["path"] == path]

    def hook_events(self) -> list[dict]:
        return [e for r in self.requests_to("/v1/telemetry/hook-events") for e in r["body"].get("events", [])]

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def server():
    s = CaptureServer()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def ouro_home(tmp_path, monkeypatch):
    home = tmp_path / "ouroboros-home"
    monkeypatch.setenv("OUROBOROS_HOME", str(home))
    monkeypatch.delenv("OUROBOROS_TELEMETRY", raising=False)
    monkeypatch.delenv("OUROBOROS_CAPTURE_PROMPTS", raising=False)
    monkeypatch.delenv("OUROBOROS_CAPTURE_TOOL_CONTENT", raising=False)
    # Hook runs must never spawn the real out-of-process sender in tests.
    monkeypatch.setenv("OUROBOROS_HOOK_NUDGE", "off")
    return home


@pytest.fixture()
def static_token():
    return lambda: "test-token"
