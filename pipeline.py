"""Local Pipecat voice pipeline with smallwebrtc transport.

Sets up a complete Pipecat voice pipeline running locally over WebRTC
(smallwebrtc transport), for development and testing before connecting to
Twilio phone lines.

Pipeline flow:
  WebRTC audio in → ElevenLabs STT → LLM context aggregator (user) → OpenAI LLM
  → LLM context aggregator (assistant) → ElevenLabs TTS → WebRTC audio out

All Pipecat / smallwebrtc / ElevenLabs / OpenAI imports live inside the
``start_local_pipeline`` function, never at module load. The plugin registers
and loads cleanly even when none of those packages are installed.

Active connections are tracked in ``ACTIVE_CONNECTIONS`` so a signaling
server (see ``frontend/signaling-server.py``) can exchange SDP with a
running pipeline and let a browser-based voice UI connect end to end.

The pipeline and signaling server must run in the SAME process because
``ACTIVE_CONNECTIONS`` and ``_ACTIVE_LOOP`` are in-process globals.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid

logger = logging.getLogger(__name__)

REQUIRED_ENV = ("ELEVENLABS_API_KEY", "OPENAI_API_KEY")

# Registry of active WebRTC connections, keyed by connection id.
# A signaling server can look up a connection here to exchange SDP with a
# running pipeline.
ACTIVE_CONNECTIONS: dict[str, object] = {}

# The event loop that the pipeline runs on. The signaling server uses this
# to schedule coroutines (connection.initialize, get_answer) from its own
# HTTP handler thread.
_ACTIVE_LOOP: asyncio.AbstractEventLoop | None = None


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
    global _ACTIVE_LOOP  # CRITICAL: without this, module global stays None

    elevenlabs_key = elevenlabs_api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    openai_key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")

    # Model names come from env with sensible defaults
    stt_model = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")
    tts_model = os.environ.get("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
    llm_model = os.environ.get("OPENAI_MODEL", "gpt-5.6-luna")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "")

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
        from pipecat.services.elevenlabs.stt import (
            ElevenLabsSTTService,
            ElevenLabsSTTSettings,
        )
        from pipecat.services.elevenlabs.tts import (
            ElevenLabsTTSService,
            ElevenLabsTTSSettings,
        )
        from pipecat.services.openai.llm import (
            OpenAILLMService,
        )
        from pipecat.services.openai.base_llm import OpenAILLMSettings
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.task import PipelineTask, PipelineParams
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.processors.aggregators.llm_context import LLMContext
        from pipecat.processors.aggregators.llm_response_universal import (
            LLMContextAggregatorPair,
            LLMUserAggregatorParams,
        )
        from pipecat.processors.frameworks.rtvi import (
            RTVIProcessor,
            RTVIObserver,
            RTVIObserverParams,
        )
    except ImportError as exc:
        return {
            "ok": False,
            "error": (
                "Pipecat or smallwebrtc not installed: "
                + str(exc)
                + ". Install pipecat-ai>=1.7.0,<1.8.0 to use the local "
                "pipeline."
            ),
        }

    # --- LLM context ---
    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_stop_timeout=5.0,
            user_idle_timeout=0,
        ),
    )

    # --- OpenAI LLM (settings pattern, avoids deprecation warning) ---
    llm = OpenAILLMService(
        api_key=openai_key,
        settings=OpenAILLMSettings(
            model=llm_model,
            system_instruction=(
                "You are a helpful assistant in a voice conversation. "
                "Keep responses concise and natural for text-to-speech. "
                "Avoid emojis, bullet points, and formatting that can't be spoken."
            ),
        ),
    )

    # --- WebRTC connection + transport ---
    connection_id = str(uuid.uuid4())
    connection = SmallWebRTCConnection()
    ACTIVE_CONNECTIONS[connection_id] = connection
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
    session = aiohttp.ClientSession()
    stt = ElevenLabsSTTService(
        api_key=elevenlabs_key,
        aiohttp_session=session,
        settings=ElevenLabsSTTSettings(model=stt_model),
    )

    # --- Text-to-speech (ElevenLabs) ---
    tts_settings_kwargs: dict = {"model": tts_model}
    if voice_id:
        tts_settings_kwargs["voice"] = voice_id
    tts = ElevenLabsTTSService(
        api_key=elevenlabs_key,
        settings=ElevenLabsTTSSettings(**tts_settings_kwargs),
    )

    # --- RTVI (real-time voice interface control channel) ---
    rtvi = RTVIProcessor(transport=transport)
    rtvi_observer = RTVIObserver(rtvi=rtvi)
    rtvi_params = RTVIObserverParams()

    # --- Build the pipeline ---
    # Order matches Pipecat 1.x docs:
    # transport.input() → STT → user context aggregator → LLM →
    #   assistant context aggregator → TTS → transport.output()
    pipeline = Pipeline(
        processors=[
            transport.input(),
            stt,
            user_aggregator,
            llm,
            assistant_aggregator,
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
    _ACTIVE_LOOP = loop
    loop.create_task(runner.run(task))

    logger.info(
        "start_local_pipeline: pipeline started (llm=openai:%s, stt=elevenlabs:%s, tts=elevenlabs:%s, transport=smallwebrtc, connection_id=%s)",
        llm_model,
        stt_model,
        tts_model,
        connection_id,
    )

    return {
        "ok": True,
        "transport": "smallwebrtc",
        "stt": "elevenlabs:" + stt_model,
        "llm": "openai:" + llm_model,
        "tts": "elevenlabs:" + tts_model,
        "connection_id": connection_id,
        "signaling_url": "http://localhost:8765",
        "note": (
            "Pipeline started and running in the background. A WebRTC client "
            "can connect by first GET /connections to discover the connection "
            "id, then POST /offer with that connectionId at the signaling URL."
        ),
    }
