"""Signaling server: origin check, input limits, unknown connection."""
from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def server():
    spec = importlib.util.spec_from_file_location("signaling_server", ROOT / "frontend" / "signaling-server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mod.SignalingHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield mod, httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def _request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    payload = json.dumps(body) if body is not None else None
    conn.request(method, path, body=payload, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def test_foreign_origin_is_rejected(server) -> None:
    _, port = server
    status, _ = _request(port, "GET", "/connections", headers={"Origin": "http://evil.example"})
    assert status == 403


def test_offer_with_unknown_connection_is_400(server) -> None:
    _, port = server
    status, _ = _request(port, "POST", "/offer", {"connectionId": "nope", "sdp": "x"})
    assert status == 400


def test_oversized_and_bad_length_are_400(server) -> None:
    _, port = server
    status, _ = _request(port, "POST", "/offer", headers={"Content-Length": str(10**9)})
    assert status == 400
    status, _ = _request(port, "POST", "/offer", headers={"Content-Length": "abc"})
    assert status == 400


def test_index_is_served(server) -> None:
    _, port = server
    status, data = _request(port, "GET", "/")
    assert status == 200 and b"<html" in data.lower()
