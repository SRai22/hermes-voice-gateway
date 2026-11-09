"""Minimal HTTP signaling server for the Hermes voice-gateway local pipeline.

Runs alongside Hermes and bridges a browser WebRTC client (frontend/index.html)
to a Pipecat pipeline started by the ``voice.start_local_pipeline`` tool.

Endpoints:
  GET    /connections          List active pipeline connections
  GET    /status/<id>          Connection status
  POST   /offer                Receive SDP offer, return SDP answer
  POST   /ice-candidate        Relay ICE candidate to the connection

The server imports ``pipeline.ACTIVE_CONNECTIONS`` and ``_ACTIVE_LOOP`` from the
plugin's ``pipeline`` module so it can schedule async coroutines
(``connection.initialize``) on the pipeline's own event loop.

Run:
  python3 frontend/signaling-server.py
  # then open frontend/index.html in a browser
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Add the plugin directory to the path so we can import pipeline
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

import pipeline  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("voice-signaling")

PORT = 8765


class SignalingHandler(BaseHTTPRequestHandler):
    """HTTP request handler for WebRTC signaling."""

    def log_message(self, format, *args):
        logger.info("%s - %s" % (self.address_string(), format % args))

    def _send_json(self, code: int, body: dict):
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):
        """Handle CORS preflight requests from browsers."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        if self.path == "/connections":
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
        body = self._read_body()

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
            # Schedule connection.initialize(sdp, "offer") on the pipeline's loop
            future = asyncio.run_coroutine_threadsafe(
                conn.initialize(sdp, sdp_type), loop
            )
            future.result(timeout=15)  # wait for initialize to complete

            # get_answer returns a dict: {"sdp": str, "type": str, "pc_id": str}
            answer = conn.get_answer()
            if answer is None:
                self._send_json(500, {"error": "no answer available"})
                return
            answer_sdp = answer["sdp"]
            answer_type = answer.get("type", "answer")

            logger.info("Signaling: answer returned for connection %s", cid)
            self._send_json(200, {
                "connectionId": cid,
                "sdp": answer_sdp,
                "type": answer_type
            })
        except asyncio.TimeoutError:
            logger.error("Signaling: initialize timed out for %s", cid)
            self._send_json(504, {"error": "initialize timed out"})
        except Exception as exc:
            logger.exception("Signaling: offer failed for %s", cid)
            self._send_json(500, {"error": str(exc)})

    def _handle_ice_candidate(self, body: dict):
        cid = body.get("connectionId")
        candidate = body.get("candidate")
        sdp_mid = body.get("sdpMid")
        sdp_mline_index = body.get("sdpMLineIndex")

        if not cid or cid not in pipeline.ACTIVE_CONNECTIONS:
            self._send_json(400, {"error": "unknown or missing connectionId"})
            return

        conn = pipeline.ACTIVE_CONNECTIONS[cid]
        try:
            from aiortc import RTCIceCandidate
            ice = RTCIceCandidate(
                candidate=candidate,
                sdpMid=sdp_mid,
                sdpMLineIndex=sdp_mline_index
            )
            conn.add_ice_candidate(ice)
            self._send_json(200, {"ok": True})
        except Exception as exc:
            logger.exception("Signaling: ice-candidate failed for %s", cid)
            self._send_json(500, {"error": str(exc)})


def main():
    server = HTTPServer(("0.0.0.0", PORT), SignalingHandler)
    logger.info("Signaling server listening on http://0.0.0.0:%d", PORT)
    logger.info("Open frontend/index.html in a browser to connect")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
