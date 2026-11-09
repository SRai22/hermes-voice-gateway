"""Local Pipecat voice pipeline with smallwebrtc transport.

Sets up a complete Pipecat voice pipeline running locally over WebRTC
(smallwebrtc transport), for development and testing before connecting to
Twilio phone lines.

Pipeline flow:
  WebRTC audio in → VAD turn detection → Deepgram STT → LLM → TTS → WebRTC audio out

All Pipecat / smallwebrtc / Deepgram / OpenAI imports live inside the
``start_local_pipeline`` function, never at module load. The plugin registers
and loads cleanly even when none of those packages are installed.
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

REQUIRED_ENV = ("DEEPGRAM_API_KEY",)


def _missing_env() -> list[str]:
    return [v for v in REQUIRED_ENV if not os.environ.get(v)]


class _EchoLLM:
    """A minimal stand-in LLM that echoes the user's message back.

    Used when no OpenAI API key is available, so the pipeline can still run
    end-to-end for local audio testing.
    """

    async def __call__(self, text: str) -> str:
        return f"You said: {text}"


async def start_local_pipeline(deepgram_api_key: str = "") -> dict:
    """Start a Pipecat pipeline with smallwebrtc local audio transport.

    Returns ``{"ok": True, ...}`` with connection info when the pipeline
    starts, or ``{"ok": False, "error": ...}`` when dependencies or
    credentials are missing.

    The pipeline runs as a background asyncio task after this call returns,
    waiting for a WebRTC client to connect to the smallwebrtc transport.
    """
    api_key = deepgram_api_key or os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        return {
            "ok": False,
            "error": (
                "DEEPGRAM_API_KEY is required for the local Pipecat pipeline. "
                "Set it in your environment or pass it to the tool."
            ),
        }

    missing = _missing_env()
    if missing:
        return {
            "ok": False,
            "error": "Missing env: " + ", ".join(missing),
        }

    try:
        # --- Pipecat / smallwebrtc imports (only at call time) ---
        from pipecat.transports.smallwebrtc.connection import (
            SmallWebRTCConnection,
        )
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
        from pipecat.transports.base_transport import TransportParams
        from pipecat.services.deepgram.stt import DeepgramSTTService
        from pipecat.services.deepgram.tts import DeepgramTTSService
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
                + ". Install pipecat-ai[webrtc,deepgram] to use the local "
                "pipeline."
            ),
        }

    # --- OpenAI LLM (optional; falls back to echo) ---
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    llm = None
    llm_name = "echo"
    if openai_key:
        try:
            from pipecat.services.openai.llm import OpenAILLMService

            llm = OpenAILLMService(model="gpt-4o-mini", api_key=openai_key)
            llm_name = "openai:gpt-4o-mini"
        except ImportError:
            logger.warning("OpenAILLMService unavailable; falling back to echo LLM")

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

    # --- Speech-to-text (Deepgram) ---
    stt = DeepgramSTTService(api_key=api_key)

    # --- Text-to-speech (Deepgram) ---
    tts = DeepgramTTSService(api_key=api_key)

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
            llm if llm is not None else _EchoLLM(),
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
        "start_local_pipeline: pipeline started (llm=%s, stt=deepgram, tts=deepgram, transport=smallwebrtc)",
        llm_name,
    )

    return {
        "ok": True,
        "transport": "smallwebrtc",
        "stt": "deepgram",
        "llm": llm_name,
        "tts": "deepgram",
        "note": (
            "Pipeline started and running in the background. Waiting for a "
            "WebRTC client to connect to the smallwebrtc transport."
        ),
    }
