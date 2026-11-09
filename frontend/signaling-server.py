"""Minimal HTTP signaling server for the Hermes voice-gateway local pipeline.

Bridges a browser WebRTC client (frontend/index.html) to the Pipecat pipeline
started by ``voice.start_local_pipeline``. It also serves index.html itself, so
the page and the API share one origin and no CORS is needed.

Endpoints:
  GET    /                     The browser UI (frontend/index.html)
  GET    /connections          List active pipeline connections
  GET    /status/<id>          Connection status
  POST   /offer                Receive SDP offer, return SDP answer
  POST   /ice-candidate        Relay ICE candidate to the connection

It listens on 127.0.0.1 only and rejects requests from other web origins.

It must run in the SAME process as the pipeline, because it reads the
in-process globals ``pipeline.ACTIVE_CONNECTIONS`` and ``pipeline._ACTIVE_LOOP``.
Start both with ``python scripts/start_local_pipeline.py``, then open
http://127.0.0.1:8765/ in a browser. Running this file alone cannot work.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Add the plugin directory to the path so we can import pipeline
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

import pipeline  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("voice-signaling")

HOST = "127.0.0.1"
PORT = pipeline.SIGNALING_PORT
ALLOWED_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
MAX_BODY = 64 * 1024
INDEX_FILE = os.path.join(PLUGIN_DIR, "frontend", "index.html")
_OFFER_LOCKS: dict[str, asyncio.Lock] = {}
_OFFER_LOCKS_LOCK = threading.Lock()


def _offer_lock(connection_id: str) -> asyncio.Lock:
    with _OFFER_LOCKS_LOCK:
        return _OFFER_LOCKS.setdefault(connection_id, asyncio.Lock())


async def _initialize_offer(conn, connection_id: str, sdp: str, sdp_type: str):
    async with _offer_lock(connection_id):
        await conn.initialize(sdp, sdp_type)
        return conn.get_answer()


class SignalingHandler(BaseHTTPRequestHandler):
    """HTTP request handler for WebRTC signaling."""

    def log_message(self, format, *args):
        logger.info("%s - %s" % (self.address_string(), format % args))

    def _send(self, code: int, payload: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, code: int, body: dict):
        self._send(code, json.dumps(body).encode("utf-8"), "application/json")

    def _origin_ok(self) -> bool:
        # Browsers always send Origin on cross-site requests. A page on another
        # site (or another machine's page) must not drive this pipeline.
        origin = self.headers.get("Origin")
        if origin is None or origin in ALLOWED_ORIGINS:
            return True
        self._send_json(403, {"error": "origin not allowed"})
        return False

    def _read_body(self) -> dict | None:
        """Return the JSON body, or None after sending a 400."""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            self._send_json(400, {"error": "bad or oversized body"})
            return None
        if length == 0:
            return {}
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            body = None
        if not isinstance(body, dict):
            self._send_json(400, {"error": "body must be a JSON object"})
            return None
        return body

    def do_GET(self):
        if not self._origin_ok():
            return
        if self.path in ("/", "/index.html"):
            with open(INDEX_FILE, "rb") as fh:
                self._send(200, fh.read(), "text/html; charset=utf-8")
        elif self.path == "/connections":
            ids = list(pipeline.ACTIVE_CONNECTIONS.keys())
            self._send_json(200, {"connections": ids, "count": len(ids)})
        elif self.path.startswith("/status/"):
            cid = self.path.split("/status/")[1]
            conn = pipeline.ACTIVE_CONNECTIONS.get(cid)
            if conn is None:
                self._send_json(404, {"error": "unknown connection"})
            else:
                try:
                    connected = conn.is_connected()
                except Exception:
                    connected = False
                self._send_json(200, {"connectionId": cid, "connected": connected})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self._origin_ok():
            return
        body = self._read_body()
        if body is None:
            return

        if self.path == "/offer":
            self._handle_offer(body)
        elif self.path == "/ice-candidate":
            self._handle_ice_candidate(body)
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_offer(self, body: dict):
        cid = body.get("connectionId")
        sdp = body.get("sdp")
        sdp_type = body.get("type", "offer")

        if not cid or cid not in pipeline.ACTIVE_CONNECTIONS:
            self._send_json(400, {"error": "unknown or missing connectionId", "available": list(pipeline.ACTIVE_CONNECTIONS.keys())})
            return
        if not sdp:
            self._send_json(400, {"error": "missing sdp"})
            return

        conn = pipeline.ACTIVE_CONNECTIONS[cid]
        loop = pipeline._ACTIVE_LOOP

        if loop is None:
            self._send_json(500, {"error": "pipeline event loop not available"})
            return

        try:
            # Keep each initialize/answer pair together; ICE requests use no offer lock.
            future = asyncio.run_coroutine_threadsafe(
                _initialize_offer(conn, cid, sdp, sdp_type), loop
            )
            answer = future.result(timeout=15)
            if answer is None:
                self._send_json(500, {"error": "no answer available"})
                return

            logger.info("Signaling: answer returned for connection %s", cid)
            self._send_json(200, {
                "connectionId": cid,
                "sdp": answer["sdp"],
                "type": answer.get("type", "answer"),
            })
        except (asyncio.TimeoutError, TimeoutError):
            logger.error("Signaling: initialize timed out for %s", cid)
            self._send_json(504, {"error": "initialize timed out"})
        except Exception as exc:
            logger.exception("Signaling: offer failed for %s", cid)
            self._send_json(500, {"error": str(exc)})

    def _handle_ice_candidate(self, body: dict):
        cid = body.get("connectionId")
        if not cid or cid not in pipeline.ACTIVE_CONNECTIONS:
            self._send_json(400, {"error": "unknown or missing connectionId"})
            return
        loop = pipeline._ACTIVE_LOOP
        if loop is None:
            self._send_json(500, {"error": "pipeline event loop not available"})
            return

        conn = pipeline.ACTIVE_CONNECTIONS[cid]
        try:
            from aiortc.sdp import candidate_from_sdp

            raw = body.get("candidate") or ""
            if raw:
                # The browser sends "candidate:<...>"; aiortc wants the part after it.
                ice = candidate_from_sdp(raw.split(":", 1)[1] if raw.startswith("candidate:") else raw)
                ice.sdpMid = body.get("sdpMid")
                ice.sdpMLineIndex = body.get("sdpMLineIndex")
            else:
                ice = None  # empty string = end-of-candidates marker (RFC 8840)
            # add_ice_candidate is async and the connection lives on the pipeline loop.
            asyncio.run_coroutine_threadsafe(conn.add_ice_candidate(ice), loop).result(timeout=5)
            self._send_json(200, {"ok": True})
        except Exception as exc:
            logger.exception("Signaling: ice-candidate failed for %s", cid)
            self._send_json(500, {"error": str(exc)})


def main():
    server = ThreadingHTTPServer((HOST, PORT), SignalingHandler)
    logger.info("Signaling server listening on http://%s:%d", HOST, PORT)
    logger.info("Open http://%s:%d/ in a browser to connect", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
