#!/usr/bin/env python3
"""Known-good runner: start the hermes-voice-gateway local Pipecat pipeline + signaling server in ONE process.

Loads secrets from ~/.hermes/.env, invokes the plugin's real
voice_start_local_pipeline handler, then runs frontend/signaling-server.py's
HTTP server (port 8765) in a daemon thread so a browser can POST SDP offers
and connect end to end.

The pipeline and signaling server share a process because signaling_server
reads pipeline.ACTIVE_CONNECTIONS and pipeline._ACTIVE_LOOP as in-process
globals captured at import time.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import sys
import threading

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

# Load secrets from ~/.hermes/.env
ENV_FILE = os.path.expanduser("~/.hermes/.env")
if os.path.exists(ENV_FILE):
    with open(ENV_FILE) as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

from pipeline import start_local_pipeline  # noqa: E402


async def main() -> int:
    result = await start_local_pipeline()
    print("PIPELINE_START_RESULT:", result, flush=True)
    if not result.get("ok"):
        print("Pipeline failed to start; see error above.", flush=True)
        return 1

    # Import AFTER start_local_pipeline ran so its
    # ``from pipeline import _ACTIVE_LOOP`` captures the live loop.
    SIGNALING_FILE = os.path.join(PLUGIN_DIR, "frontend", "signaling-server.py")
    spec = importlib.util.spec_from_file_location("signaling_server", SIGNALING_FILE)
    signaling_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(signaling_server)  # noqa: E402

    server_thread = threading.Thread(
        target=signaling_server.main, daemon=True, name="voice-signaling"
    )
    server_thread.start()
    print("Signaling server started on http://127.0.0.1:8765", flush=True)

    stop = asyncio.get_event_loop().create_future()
    try:
        await stop
    except asyncio.CancelledError:
        pass
    print("Pipeline stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
