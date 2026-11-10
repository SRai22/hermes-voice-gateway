"""Local Pipecat voice pipeline with smallwebrtc transport.

Sets up a complete Pipecat voice pipeline running locally over WebRTC
(smallwebrtc transport), for development and testing before connecting to
Twilio phone lines.

Pipeline flow:
  WebRTC audio in → VAD → ElevenLabs STT → user aggregator (smart-turn end-of-speech)
  → OpenAI LLM → ElevenLabs TTS → WebRTC audio out → assistant aggregator

Call behaviour (greeting, voice profile, guardrails) lives in ``voice_behavior``.

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

try:  # plugin package under Hermes
    from ._env import secret
except ImportError:  # imported top-level by the dev scripts
    from _env import secret
try:
    from . import voice_behavior as vb
except ImportError:
    import voice_behavior as vb

logger = logging.getLogger(__name__)

# Keys the local dev pipeline needs (hooks.py imports it for voice_start_local_pipeline).
REQUIRED_ENV = ("ELEVENLABS_API_KEY", "OPENAI_API_KEY")

SIGNALING_PORT = 8765

IDLE_SECONDS = 15.0  # silence before "Are you still there?"

# Registry of active WebRTC connections, keyed by connection id.
# A signaling server can look up a connection here to exchange SDP with a
# running pipeline.
ACTIVE_CONNECTIONS: dict[str, object] = {}

# The event loop that the pipeline runs on. The signaling server uses this
# to schedule coroutines (connection.initialize, get_answer) from its own
# HTTP handler thread.
_ACTIVE_LOOP: asyncio.AbstractEventLoop | None = None

# Port the signaling server listens on. Must match what signaling-server.py
# expects via pipeline.SIGNALING_PORT.
SIGNALING_PORT = 8765

# The running pipeline task and its connection id. Held here so the task is not
# garbage-collected and so a second call can reuse the running pipeline.
_RUN_TASK: asyncio.Task | None = None
_RUN_INFO: dict = {}


async def _run_pipeline(runner, task, session, connection_id: str) -> None:
    """Run the pipeline, then release its HTTP session and connection entry."""
    try:
        await runner.add_workers(task)
        await runner.run()
    except Exception:
        logger.exception("pipeline %s crashed", connection_id)
    finally:
        await session.close()
        ACTIVE_CONNECTIONS.pop(connection_id, None)


async def _stop_pipeline_on_disconnect(connection_id: str) -> None:
    """Cancel the pipeline and wait for cleanup when its peer disconnects."""
    run_task = _RUN_TASK
    if (
        run_task is None
        or run_task.done()
        or _RUN_INFO.get("connection_id") != connection_id
    ):
        return

    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass


async def start_local_pipeline() -> dict:
    """Start a Pipecat pipeline with smallwebrtc local audio transport.

    Keys come from the environment only (never from tool arguments).
    Returns ``{"ok": True, ...}`` with connection info when the pipeline
    starts, or ``{"ok": False, "error": ...}`` when dependencies or
    credentials are missing. If a pipeline is already running, returns its
    info instead of starting a second one.

    The pipeline runs as a background asyncio task after this call returns,
    waiting for a WebRTC client to connect to the smallwebrtc transport.
    """
    global _ACTIVE_LOOP, _RUN_TASK, _RUN_INFO  # without global, these stay None

    if _RUN_TASK is not None and not _RUN_TASK.done():
        return {**_RUN_INFO, "already_running": True}

    elevenlabs_key = secret("ELEVENLABS_API_KEY", "")
    openai_key = secret("OPENAI_API_KEY", "")

    # Model names come from env with sensible defaults
    stt_model = secret("ELEVENLABS_STT_MODEL", "scribe_v2")
    tts_model = secret("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
    llm_model = secret("OPENAI_MODEL", "gpt-5.6-luna")
    voice_id = secret("ELEVENLABS_VOICE_ID", "")

    missing = [v for v in REQUIRED_ENV if not secret(v)]
    if missing:
        return {
            "ok": False,
            "error": "Missing required keys: " + ", ".join(missing) + ". Set them in your environment.",
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
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.worker import PipelineWorker, PipelineParams
        from pipecat.workers.runner import WorkerRunner
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
        from pipecat.audio.vad.silero import SileroVADAnalyzer
        from pipecat.processors.audio.vad_processor import VADProcessor
        from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
            LocalSmartTurnAnalyzerV3,
        )
        from pipecat.frames.frames import EndFrame, TTSSpeakFrame
        from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
        from pipecat.turns.user_turn_strategies import UserTurnStrategies
    except ImportError as exc:
        return {
            "ok": False,
            "error": (
                "Pipecat or smallwebrtc not installed: "
                + str(exc)
                + ". Install pipecat-ai[elevenlabs,openai,silero,webrtc]"
                ">=1.15.0,<1.16.0 to use the local pipeline."
            ),
        }

    # --- LLM context ---
    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            # Smart turn: a local model decides from the audio whether the caller
            # has finished, instead of replying after any short pause.
            user_turn_strategies=UserTurnStrategies(
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())]
            ),
            user_idle_timeout=IDLE_SECONDS,
        ),
    )

    # --- OpenAI LLM (settings pattern, avoids deprecation warning) ---
    llm = OpenAILLMService(
        api_key=openai_key,
        settings=OpenAILLMSettings(
            model=llm_model,
            system_instruction=vb.load_profile(),
        ),
    )

    # --- WebRTC connection + transport ---
    connection_id = str(uuid.uuid4())
    connection = SmallWebRTCConnection()
    ACTIVE_CONNECTIONS[connection_id] = connection

    async def on_disconnect(_connection) -> None:
        await _stop_pipeline_on_disconnect(connection_id)

    connection.event_handler("disconnected")(on_disconnect)
    connection.event_handler("failed")(on_disconnect)

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

    # --- VAD (voice activity detection) — required by SegmentedSTTService
    # (ElevenLabsSTTService) to trigger transcription on speech segments ---
    vad = VADProcessor(vad_analyzer=SileroVADAnalyzer())

    # --- Build the pipeline ---
    # Order: transport.input() → VAD → STT → user context aggregator → LLM →
    #   TTS → transport.output().
    # NOTE: assistant_aggregator (from LLMContextAggregatorPair) swallows
    # LLMTextFrame frames (buffers into context only, never pushes downstream),
    # so it must NOT sit between LLM and TTS or TTS never receives text. After
    # transport.output() it records what was actually spoken (greeting included).
    pipeline = Pipeline(
        processors=[
            transport.input(),
            vad,
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ],
    )

    # --- Pipeline task + runner ---
    task = PipelineWorker(
        pipeline,
        params=PipelineParams(),
        enable_rtvi=True,
        rtvi_processor=rtvi,
        rtvi_observer_params=rtvi_params,
    )
    runner = WorkerRunner()

    # --- Greeting ---
    opening = vb.greeting()

    @transport.event_handler("on_client_connected")
    async def _greet(_transport, _client) -> None:
        if opening:
            await task.queue_frame(TTSSpeakFrame(opening))

    # --- Guardrails: spoken fallbacks, then hang up if the call stays broken ---
    guard = vb.CallGuard()

    async def say(text: str, hang_up: bool) -> None:
        await task.queue_frame(TTSSpeakFrame(text))
        if hang_up:
            await task.queue_frame(EndFrame())

    @task.event_handler("on_pipeline_error")
    async def _on_error(_task, frame) -> None:
        logger.warning("pipeline error: %s", getattr(frame, "error", frame))
        await say(*guard.on_error())

    @user_aggregator.event_handler("on_user_turn_idle")
    async def _on_idle(_aggregator) -> None:
        await say(*guard.on_idle())

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def _on_turn(_aggregator, _strategy, message) -> None:
        text = guard.on_turn(message.content)  # sound with no words -> ask to repeat
        if text:
            await say(text, False)

    # Start the runner as a background task so this function returns
    # immediately while the pipeline keeps running and waiting for a
    # WebRTC client to connect.
    _ACTIVE_LOOP = asyncio.get_running_loop()
    _RUN_TASK = _ACTIVE_LOOP.create_task(_run_pipeline(runner, task, session, connection_id))

    logger.info(
        "start_local_pipeline: pipeline started (llm=openai:%s, stt=elevenlabs:%s, tts=elevenlabs:%s, transport=smallwebrtc, connection_id=%s)",
        llm_model,
        stt_model,
        tts_model,
        connection_id,
    )

    _RUN_INFO = {
        "ok": True,
        "transport": "smallwebrtc",
        "stt": "elevenlabs:" + stt_model,
        "llm": "openai:" + llm_model,
        "tts": "elevenlabs:" + tts_model,
        "connection_id": connection_id,
        "signaling_url": f"http://127.0.0.1:{SIGNALING_PORT}",
        "note": (
            "Pipeline started and running in the background. Run "
            "scripts/start_local_pipeline.py (same process) and open the "
            "signaling URL in a browser to connect."
        ),
    }
    return _RUN_INFO
