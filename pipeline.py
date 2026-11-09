"""Local Pipecat voice pipeline with smallwebrtc transport.

Sets up a complete Pipecat voice pipeline running locally over WebRTC
(smallwebrtc transport), for development and testing before connecting to
Twilio phone lines.

Pipeline flow:
  WebRTC audio in → VAD turn detection → ElevenLabs STT → OpenAI LLM → ElevenLabs TTS → WebRTC audio out

All Pipecat / smallwebrtc / ElevenLabs / OpenAI imports live inside the
``start_local_pipeline`` function, never at module load. The plugin registers
and loads cleanly even when none of those packages are installed.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

REQUIRED_ENV = ("ELEVENLABS_API_KEY", "OPENAI_API_KEY")


def _missing_env() -> list[str]:
    return [v for v in REQUIRED_ENV if not os.environ.get(v)]


async def start_local_pipeline(
    elevenlabs_api_key: str = "", openai_api_key: str = ""
) -> dict:
    """Start a Pipecat pipeline with smallwebrtc local audio transport.

    Returns ``{"ok": True, ...}`` with connection info when the pipeline
    starts, or ``{"ok": False, "error": ...}`` when dependencies or
    credentials are missing.

    The pipeline runs as a background asyncio task after this call returns,
    waiting for a WebRTC client to connect to the smallwebrtc transport.
    """
    elevenlabs_key = elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    openai_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")

    missing = []
    if not elevenlabs_key:
        missing.append("ELEVENLABS_API_KEY")
    if not openai_key:
        missing.append("OPENAI_API_KEY")
    if missing:
        return {
            "ok": False,
            "error": (
                "Missing required keys: "
                + ", ".join(missing)
                + ". Set them in your environment or pass them to the tool."
            ),
        }

    try:
        # --- Pipecat / smallwebrtc imports (only at call time) ---
        import aiohttp

        from pipecat.transports.smallwebrtc.connection import (
            SmallWebRTCConnection,
        )
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
        from pipecat.transports.base_transport import TransportParams
        from pipecat.services.elevenlabs.stt import ElevenLabsSTTService
        from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
        from pipecat.services.openai.llm import OpenAILLMService
        from pipecat.turns.user_turn_processor import UserTurnProcessor
        from pipecat.turns.user_start import VADUserTurnStartStrategy
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineTask, PipelineParams
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.processors.frameworks.rtvi import (
            RTVIProcessor,
            RTVIObserver,
            RTVIObserverParams,
        )
        from pipecat.processors.aggregators.llm_response import (
            LLMFullResponseAggregator,
        )
    except ImportError as exc:
        return {
            "ok": False,
            "error": (
                "Pipecat or smallwebrtc not installed: "
                + str(exc)
                + ". Install pipecat-ai[webrtc,elevenlabs] to use the local "
                "pipeline."
            ),
        }

    # --- OpenAI LLM (actual LLM path, no echo fallback) ---
    llm = OpenAILLMService(model="gpt-4o-mini", api_key=openai_key)

    # --- WebRTC connection + transport ---
    connection = SmallWebRTCConnection()
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_sample_rate=16000,
        ),
        input_name="mic_input",
        output_name="speaker_output",
    )

    # --- Speech-to-text (ElevenLabs) ---
    # ElevenLabsSTTService requires an aiohttp session. We create one here;
    # it lives for the lifetime of the pipeline (no explicit cleanup for a
    # short-lived local dev pipeline).
    session = aiohttp.ClientSession()
    stt = ElevenLabsSTTService(api_key=elevenlabs_key, aiohttp_session=session)

    # --- Text-to-speech (ElevenLabs) ---
    tts = ElevenLabsTTSService(api_key=elevenlabs_key)

    # --- Turn detection (VAD-based) ---
    turn_processor = UserTurnProcessor(
        user_turn_strategies=[VADUserTurnStartStrategy()],
        user_turn_stop_timeout=5.0,
        user_idle_timeout=0,
    )

    # --- LLM response aggregation ---
    response_aggregator = LLMFullResponseAggregator()

    # --- RTVI (real-time voice interface control channel) ---
    rtvi = RTVIProcessor(transport=transport)
    rtvi_observer = RTVIObserver(rtvi=rtvi)
    rtvi_params = RTVIObserverParams()

    # --- Build the pipeline ---
    # transport.input()  → audio from WebRTC client
    # transport.output() → audio to WebRTC client
    pipeline = Pipeline(
        processors=[
            transport.input(),
            turn_processor,
            stt,
            llm,
            response_aggregator,
            tts,
            transport.output(),
        ],
    )

    # --- Pipeline task + runner ---
    task = PipelineTask(
        pipeline,
        params=PipelineParams(),
        enable_rtvi=True,
        rtvi_processor=rtvi,
        rtvi_observer_params=rtvi_params,
    )
    runner = PipelineRunner()

    # Start the runner as a background task so this function returns
    # immediately while the pipeline keeps running and waiting for a
    # WebRTC client to connect.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.create_task(runner.run(task))

    logger.info(
        "start_local_pipeline: pipeline started (llm=openai:gpt-4o-mini, stt=elevenlabs, tts=elevenlabs, transport=smallwebrtc)"
    )

    return {
        "ok": True,
        "transport": "smallwebrtc",
        "stt": "elevenlabs",
        "llm": "openai:gpt-4o-mini",
        "tts": "elevenlabs",
        "note": (
            "Pipeline started and running in the background. Waiting for a "
            "WebRTC client to connect to the smallwebrtc transport."
        ),
    }
