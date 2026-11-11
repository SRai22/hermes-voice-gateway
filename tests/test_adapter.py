"""VoiceAdapter: webhook auth, media-stream auth, Hermes <-> call plumbing.

Hermes' ``gateway`` package is replaced by stubs; Pipecat is not needed here.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import sys
import types
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hermes_stubs import load_adapter_module, use_memory_db  # noqa: E402

BASE = "https://example.test"
TOKEN = "auth-token"
CALLER = "+15550001111"
ENV = {
    "TWILIO_ACCOUNT_SID": "AC1",
    "TWILIO_AUTH_TOKEN": TOKEN,
    "ELEVENLABS_API_KEY": "el",
    "VOICE_WEBHOOK_URL": BASE,
    "VOICE_ALLOWED_USERS": CALLER,
}


def _sign(params: dict) -> str:
    data = f"{BASE}/voice" + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()


@pytest.fixture()
def make(monkeypatch):
    def _make(**overrides):
        for k in ("VOICE_ALLOW_ALL_USERS", "VOICE_INSECURE_NO_SIGNATURE", "VOICE_MAX_CALLS"):
            monkeypatch.delenv(k, raising=False)
        for k, v in {**ENV, **overrides}.items():
            monkeypatch.setenv(k, v)
        mod = load_adapter_module()
        use_memory_db(monkeypatch)
        monkeypatch.setattr(mod.caller_tools, "verify_tool_surface", lambda: None)
        adapter = mod.VoiceAdapter(config=object())
        return adapter, TestClient(adapter._build_app())

    return _make


def _post_voice(client, params, signature=None, headers=None):
    h = {"content-type": "application/x-www-form-urlencoded", **(headers or {})}
    if signature is not False:
        h["x-twilio-signature"] = signature if signature is not None else _sign(params)
    return client.post("/voice", content=urllib.parse.urlencode(params), headers=h)


PARAMS = {"From": CALLER, "CallSid": "CA1", "To": "+15552223333"}


def test_signed_call_from_allowed_number_gets_stream_twiml(make) -> None:
    adapter, client = make()
    resp = _post_voice(client, PARAMS)
    assert resp.status_code == 200
    assert 'url="wss://example.test/media"' in resp.text
    token = resp.text.split('name="token" value="')[1].split('"')[0]
    assert adapter._tokens.redeem(token) == {"caller": CALLER, "call_sid": "CA1"}


@pytest.mark.parametrize("signature", [False, "", "bad-signature"])
def test_missing_or_bad_signature_is_403_and_opens_no_stream(make, signature) -> None:
    _, client = make()
    resp = _post_voice(client, PARAMS, signature=signature)
    assert resp.status_code == 403
    assert "<Connect>" not in resp.text


def test_caller_not_on_allowlist_is_hung_up(make) -> None:
    _, client = make()
    params = {**PARAMS, "From": "+15559998888"}
    resp = _post_voice(client, params)
    assert "<Hangup/>" in resp.text and "<Connect>" not in resp.text


def test_second_call_from_same_number_and_over_limit_are_busy(make) -> None:
    adapter, client = make(VOICE_MAX_CALLS="1")
    adapter._calls[CALLER] = object()
    resp = _post_voice(client, PARAMS)
    assert "busy" in resp.text and "<Connect>" not in resp.text


def test_oversized_body_is_413(make) -> None:
    _, client = make()
    resp = client.post("/voice", content=b"a=" + b"x" * 70_000, headers={"x-twilio-signature": "x"})
    assert resp.status_code == 413


def test_insecure_mode_skips_signature(make) -> None:
    _, client = make(VOICE_INSECURE_NO_SIGNATURE="true")
    assert "<Connect>" in _post_voice(client, PARAMS, signature=False).text


def test_config_problems_are_reported(make) -> None:
    adapter, _ = make(VOICE_ALLOWED_USERS="")
    assert "VOICE_ALLOWED_USERS" in adapter._config_problem()
    adapter, _ = make(VOICE_WEBHOOK_URL="")
    assert "VOICE_WEBHOOK_URL" in adapter._config_problem()
    adapter, _ = make()
    assert adapter._config_problem() == ""


def test_media_stream_without_valid_token_is_refused(make) -> None:
    _, client = make()
    with client.websocket_connect("/media") as ws:
        ws.send_text("{}")
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1008


def test_send_without_active_call_fails_cleanly(make) -> None:
    adapter, _ = make()
    result = asyncio.run(adapter.send(CALLER, "hello"))
    assert result.success is False


@pytest.mark.parametrize("reply_to,metadata", [
    ("CA-old-1", {"notify": True}),
    (None, {"notify": True, "thread_id": "CA-old"}),
    ("CA-old-1", {"notify": True, "thread_id": "CA-new"}),
    (None, {"notify": True}),
])
def test_delayed_or_unscoped_reply_cannot_reach_a_later_call(make, monkeypatch, reply_to, metadata):
    adapter, _ = make()
    mod = sys.modules[adapter.__class__.__module__]
    call = mod._Call(caller=CALLER, call_sid="CA-new", turns=1,
                     task=types.SimpleNamespace(has_finished=lambda: False))
    adapter._calls[CALLER] = call
    spoken = []

    async def say(call, text):
        spoken.append(text)

    monkeypatch.setattr(adapter, "_say", say)
    result = asyncio.run(adapter.send(CALLER, "old response", reply_to=reply_to, metadata=metadata))
    assert not result.success
    assert spoken == []


@pytest.mark.parametrize("notify", [None, False, True])
def test_only_final_user_replies_are_spoken(make, monkeypatch, notify):
    adapter, _ = make()
    mod = sys.modules[adapter.__class__.__module__]
    call = mod._Call(caller=CALLER, call_sid="CA-current", turns=1,
                     task=types.SimpleNamespace(has_finished=lambda: False))
    adapter._calls[CALLER] = call
    spoken = []

    async def say(call, text):
        spoken.append(text)

    monkeypatch.setattr(adapter, "_say", say)
    metadata = {"thread_id": call.call_sid}
    if notify is not None:
        metadata["notify"] = notify
    result = asyncio.run(adapter.send(CALLER, "message", reply_to="CA-current-1", metadata=metadata))
    assert result.success
    assert spoken == (["message"] if notify is True else [])


def test_send_speaks_on_the_callers_live_pipeline(make, monkeypatch) -> None:
    adapter, _ = make()
    frames_mod = types.ModuleType("pipecat.frames.frames")
    frames_mod.TTSSpeakFrame = lambda text: ("speak", text)
    for name in ("pipecat", "pipecat.frames"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "pipecat.frames.frames", frames_mod)

    queued = []

    class FakeTask:
        finished = False

        def has_finished(self):
            return self.finished

        async def queue_frame(self, frame):
            queued.append(frame)

    adapter._calls[CALLER] = sys.modules[adapter.__class__.__module__]._Call(
        caller=CALLER, call_sid="CA1", task=FakeTask())
    result = asyncio.run(adapter.send(CALLER, "  Hi there  ", metadata={"thread_id": "CA1", "notify": True}))
    assert result.success and queued == [("speak", "Hi there")]
    assert asyncio.run(adapter.send("+19990000000", "x")).success is False
    adapter._calls[CALLER].ending = True
    assert asyncio.run(adapter.send(CALLER, "after goodbye")).success is False
    adapter._calls[CALLER].ending = False
    adapter._calls[CALLER].task.finished = True  # caller hung up
    assert asyncio.run(adapter.send(CALLER, "too late")).success is False
    assert queued == [("speak", "Hi there")]


def test_transcript_becomes_a_hermes_text_message(make) -> None:
    adapter, _ = make()
    call = sys.modules[adapter.__class__.__module__]._Call(caller=CALLER, call_sid="CA1")
    adapter._calls[CALLER] = call

    async def run():
        adapter._relay_to_hermes(call, "what are your hours")
        await adapter.wait_for_messages()
        await adapter.finish_processing(adapter.handled[0])
        await asyncio.gather(*adapter._background_tasks)

    asyncio.run(run())
    (event,) = adapter.handled
    assert event.text == "what are your hours"
    assert event.message_type == "text"  # TEXT so Hermes' own auto-TTS does not double-speak
    assert event.source.chat_id == CALLER
    assert event.source.thread_id == "CA1"
    assert event.message_id == "CA1-1"
    assert event.allow_gateway_control is False  # spoken words must not run /commands


def test_call_profile_is_snapshotted_and_sent_without_changing_transcript(make, monkeypatch, tmp_path):
    profile = tmp_path / "PROFILE.md"
    profile.write_text("Speak as the appointment receptionist.")
    monkeypatch.setenv("VOICE_PROFILE_PATH", str(profile))
    adapter, _ = make()
    mod = sys.modules[adapter.__class__.__module__]
    call = mod._Call(caller=CALLER, call_sid="CA-profile")
    adapter._calls[CALLER] = call
    profile.write_text("Changed for the next call.")

    async def run():
        for text in ("  /reset  ", "callback purpose"):
            adapter._relay_to_hermes(call, text)
        for count in (1, 2):
            await adapter.wait_for_messages(count)
            await adapter.finish_processing(adapter.handled[count - 1])
        await asyncio.gather(*adapter._background_tasks)

    asyncio.run(run())
    assert [e.text for e in adapter.handled] == ["  /reset  ", "callback purpose"]
    assert all(e.channel_prompt.startswith("Speak as the appointment receptionist.\n\n") for e in adapter.handled)
    assert all(mod.caller_tools.CALLER_INSTRUCTIONS in e.channel_prompt for e in adapter.handled)
    assert all(e.allow_gateway_control is False for e in adapter.handled)
    assert mod._Call(caller=CALLER, call_sid="next").profile == "Changed for the next call."


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_connect_serves_health_and_disconnect_stops_server(make) -> None:
    import httpx

    adapter, _ = make(VOICE_WEBHOOK_PORT=str(_free_port()))

    async def run():
        assert await adapter.connect() is True
        async with httpx.AsyncClient() as http:
            resp = await http.get(f"http://127.0.0.1:{adapter._port}/health")
        await adapter.disconnect()
        return resp

    resp = asyncio.run(run())
    assert resp.json() == {"ok": True}
    assert adapter.connected is False and adapter._server is None


def test_connect_refuses_bad_config_without_starting_a_server(make) -> None:
    adapter, _ = make(VOICE_ALLOWED_USERS="")
    assert asyncio.run(adapter.connect()) is False
    assert adapter.fatal[0] == "voice_misconfigured" and adapter._server is None


def test_chunked_oversized_body_is_413(make) -> None:
    _, client = make()

    def body():
        for _ in range(80):
            yield b"x" * 1024

    resp = client.post("/voice", content=body(), headers={"x-twilio-signature": "x"})
    assert resp.status_code == 413


def test_pending_streams_count_toward_the_call_cap(make) -> None:
    other = "+15553334444"
    adapter, client = make(VOICE_MAX_CALLS="1", VOICE_ALLOWED_USERS=f"{CALLER},{other}")
    assert "<Connect>" in _post_voice(client, PARAMS).text
    # same number again, and a second number, are both turned away while the first stream is pending
    assert "busy" in _post_voice(client, {**PARAMS, "CallSid": "CA2"}).text
    assert "busy" in _post_voice(client, {**PARAMS, "From": other, "CallSid": "CA3"}).text


def test_http_webhook_url_is_rejected(make) -> None:
    adapter, _ = make(VOICE_WEBHOOK_URL="http://example.test")
    assert "https://" in adapter._config_problem()


def test_silent_media_connection_times_out(make, monkeypatch) -> None:
    adapter, client = make()
    monkeypatch.setattr(sys.modules[adapter.__class__.__module__], "HANDSHAKE_TIMEOUT", 0.05)

    async def never(_ws):
        await asyncio.sleep(60)

    for name in ("pipecat", "pipecat.runner"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    utils = types.ModuleType("pipecat.runner.utils")
    utils.parse_telephony_websocket = never
    monkeypatch.setitem(sys.modules, "pipecat.runner.utils", utils)
    with client.websocket_connect("/media") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 1008


def test_end_call_closes_websocket_when_pipeline_not_built(make) -> None:
    adapter, _ = make()
    closed = []

    class WS:
        async def close(self):
            closed.append(True)

    call = sys.modules[adapter.__class__.__module__]._Call(caller=CALLER, call_sid="CA1", websocket=WS())
    asyncio.run(adapter._end_call(call))
    assert closed == [True]


def test_call_pipeline_builds_with_real_pipecat(make, monkeypatch) -> None:
    """Needs pipecat installed. Catches renamed classes/kwargs on a Pipecat upgrade."""
    pytest.importorskip("pipecat")
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.workers.runner import WorkerRunner

    monkeypatch.delenv("ELEVENLABS_STT_MODEL", raising=False)
    adapter, _ = make()
    seen = {}

    async def fake_add(self, *workers):  # no network: just record the worker
        seen["task"] = workers[0]
        def collect(processor):
            yield processor
            for child in getattr(processor, "processors", []):
                yield from collect(child)
        seen["processors"] = list(collect(workers[0].pipeline))

    async def fake_run(self, *args, **kwargs):
        return None

    monkeypatch.setattr(WorkerRunner, "add_workers", fake_add)
    monkeypatch.setattr(WorkerRunner, "run", fake_run)

    class FakeWebSocket:  # FastAPIWebsocketTransport only stores it until the pipeline starts
        pass

    call = sys.modules[adapter.__class__.__module__]._Call(caller=CALLER, call_sid="CA1")
    asyncio.run(adapter._run_call(FakeWebSocket(), call, "MZ1"))
    assert isinstance(call.task, PipelineWorker) and seen["task"] is call.task
    from pipecat.processors.audio.vad_processor import VADProcessor
    from pipecat.services.elevenlabs.stt import ElevenLabsSTTService

    processors = seen["processors"]
    stt = next(p for p in processors if isinstance(p, ElevenLabsSTTService))
    vad = next(p for p in processors if isinstance(p, VADProcessor))
    assert processors.index(vad) < processors.index(stt)
    assert adapter._stt_model == "scribe_v2"
    assert stt._session.closed


@pytest.fixture()
def exercise_call(make, monkeypatch):
    """Build real Pipecat services; replace only network-running/queue ingress."""
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.workers.runner import WorkerRunner
    from pipecat.transports.websocket.fastapi import FastAPIWebsocketTransport
    seen = {}
    original = FastAPIWebsocketTransport.__init__

    def init(transport, *args, **kwargs):
        original(transport, *args, **kwargs)
        seen["transport"] = transport

    async def add(runner, *workers):
        seen["worker"] = workers[0]
        def collect(processor):
            yield processor
            for child in getattr(processor, "processors", []):
                yield from collect(child)
        seen["processors"] = list(collect(workers[0].pipeline))

    async def queue(worker, frame):
        seen["frames"].append(frame)

    monkeypatch.setattr(FastAPIWebsocketTransport, "__init__", init)
    monkeypatch.setattr(WorkerRunner, "add_workers", add)
    monkeypatch.setattr(PipelineWorker, "queue_frame", queue)

    async def build(exercise, opening="", **env):
        adapter, _ = make(**env)
        mod = sys.modules[adapter.__class__.__module__]
        call = mod._Call(caller=CALLER, call_sid="CA-built", opening=opening)
        adapter._calls[CALLER] = call
        seen["frames"] = []
        seen["adapter"], seen["call"], seen["mod"] = adapter, call, mod

        async def run(runner):
            await exercise(seen)

        monkeypatch.setattr(WorkerRunner, "run", run)
        try:
            await adapter._run_call(object(), call, "MZ-built")
        finally:
            await adapter.cancel_background_tasks()
        return seen

    return build


async def _emit(obj, name, *args):
    # Invoke registered real event callbacks synchronously, surfacing assertions.
    for handler in obj._event_handlers[name].handlers:
        await handler(obj, *args)


@pytest.mark.parametrize("greeting", ["Hello from reception.", "off"])
def test_inbound_greeting_and_outbound_hermes_first(exercise_call, greeting):
    from pipecat.frames.frames import TTSSpeakFrame

    async def inbound(s):
        await _emit(s["transport"], "on_client_connected", None)
        assert [f.text for f in s["frames"] if isinstance(f, TTSSpeakFrame)] == (
            [] if greeting == "off" else [greeting])
        assert s["adapter"].handled == []

    async def outbound(s):
        await _emit(s["transport"], "on_client_connected", None)
        await s["adapter"].wait_for_messages()
        assert not any(isinstance(f, TTSSpeakFrame) for f in s["frames"])
        event, = s["adapter"].handled
        assert event.text == "Approved callback purpose"
        assert event.channel_prompt.startswith(s["call"].profile + "\n\n")

    asyncio.run(exercise_call(inbound, VOICE_GREETING=greeting))
    asyncio.run(exercise_call(outbound, opening="Approved callback purpose", VOICE_GREETING=greeting))


def test_smart_turn_relays_one_completed_turn_and_guardrails(exercise_call, monkeypatch):
    from importlib.metadata import version
    from pipecat.frames.frames import EndFrame, TTSSpeakFrame, LLMContextFrame, BotStoppedSpeakingFrame
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator, LLMAssistantAggregator
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
    from pipecat.processors.frame_processor import FrameDirection
    assert version("pipecat-ai") == "1.7.0"

    async def exercise(s):
        processors = s["processors"]
        user = next((p for p in processors if isinstance(p, LLMUserAggregator)), None)
        assert user is not None, "real smart-turn user aggregator is missing"
        assistant = next(p for p in processors if isinstance(p, LLMAssistantAggregator))
        tts = next(p for p in processors if isinstance(p, ElevenLabsTTSService))
        assert processors.index(user) < processors.index(tts) < processors.index(assistant)
        stop, = user._params.user_turn_strategies.stop
        assert isinstance(stop, TurnAnalyzerUserTurnStopStrategy)
        assert isinstance(stop._turn_analyzer, LocalSmartTurnAnalyzerV3)
        assert user._params.user_idle_timeout == 15
        sink = processors[processors.index(user) + 1]
        pushed = []

        async def push(frame, direction):
            pushed.append(frame)

        monkeypatch.setattr(sink, "push_frame", push)
        await sink.process_frame(LLMContextFrame(user._context), FrameDirection.DOWNSTREAM)
        control = BotStoppedSpeakingFrame()
        await sink.process_frame(control, FrameDirection.UPSTREAM)
        assert pushed == [control], "context sink must retain playback/control frames"
        assert not s["adapter"].handled
        from pipecat.frames.frames import TranscriptionFrame

        # Exercise the real aggregator: an inference-triggered first segment
        # plus a later transcript must still produce ONE full-turn relay.
        async def deliver_context(frame, direction=FrameDirection.DOWNSTREAM):
            await sink.process_frame(frame, direction)

        monkeypatch.setattr(user, "push_frame", deliver_context)
        await user._handle_transcription(TranscriptionFrame("first fragment", "caller", "now"))
        await user._on_user_turn_inference_triggered(user._user_turn_controller, stop)
        assert not s["adapter"].handled
        await user._handle_transcription(TranscriptionFrame("second fragment", "caller", "now"))
        await user._maybe_emit_user_turn_stopped(stop)
        await asyncio.gather(*(task for _, task in user._event_tasks))
        await s["adapter"].wait_for_messages()
        assert [e.text for e in s["adapter"].handled] == ["first fragment second fragment"]
        await _emit(user, "on_user_turn_stopped", None, types.SimpleNamespace(content=""))
        assert any(isinstance(f, TTSSpeakFrame) and f.text == s["mod"].vb.NOT_UNDERSTOOD for f in s["frames"])
        for _ in range(3):
            await _emit(s["worker"], "on_pipeline_error", types.SimpleNamespace(error="provider failed"))
        assert [f.text for f in s["frames"] if isinstance(f, TTSSpeakFrame)][-1] == s["mod"].vb.ERROR_GOODBYE
        assert isinstance(s["frames"][-1], EndFrame)

    asyncio.run(exercise_call(exercise))


def test_idle_waits_for_hermes_completion_and_all_queued_playback(exercise_call, monkeypatch):
    from pipecat.frames.frames import (TTSSpeakFrame, TTSStoppedFrame, BotStartedSpeakingFrame,
                                       BotStoppedSpeakingFrame, UserIdleTimeoutUpdateFrame, EndFrame)
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator, LLMAssistantAggregator
    from pipecat.processors.frame_processor import FrameDirection

    async def exercise(s):
        adapter, call = s["adapter"], s["call"]
        user = next(p for p in s["processors"] if isinstance(p, LLMUserAggregator))
        assistant = next(p for p in s["processors"] if isinstance(p, LLMAssistantAggregator))
        playback = s["processors"][s["processors"].index(assistant) - 1]
        sink = s["processors"][s["processors"].index(user) + 1]

        async def ignore(*args):
            pass

        monkeypatch.setattr(playback, "push_frame", ignore)
        monkeypatch.setattr(sink, "push_frame", ignore)
        await _emit(user, "on_user_turn_stopped", None, types.SimpleNamespace(content="slow question"))
        await adapter.wait_for_messages()
        event, = adapter.handled
        await _emit(user, "on_user_turn_idle")
        assert not any(isinstance(f, TTSSpeakFrame) for f in s["frames"]), "admission is not inference completion"
        assert [f.timeout for f in s["frames"] if isinstance(f, UserIdleTimeoutUpdateFrame)][-1] == 0
        await adapter.on_processing_start(event)
        await adapter.send(CALLER, "part one", reply_to=event.message_id, metadata={"notify": True})
        await adapter.send(CALLER, "part two", reply_to=event.message_id, metadata={"notify": True})
        await adapter.on_processing_complete(event, types.SimpleNamespace(value="success"))
        await _emit(user, "on_user_turn_idle")
        assert call.guard.idles == 0, "completion must not rearm ahead of queued audio"
        await sink.process_frame(BotStartedSpeakingFrame(), FrameDirection.UPSTREAM)
        await sink.process_frame(BotStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        await playback.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        await _emit(user, "on_user_turn_idle")
        assert call.guard.idles == 0, "one playback stop must not clear two queued utterances"
        await playback.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        assert [f.timeout for f in s["frames"] if isinstance(f, UserIdleTimeoutUpdateFrame)][-1] == 15
        await _emit(user, "on_user_turn_idle")
        assert call.guard.idles == 1
        assert [f.text for f in s["frames"] if isinstance(f, TTSSpeakFrame)][-1] == s["mod"].vb.IDLE_PROMPT
        await playback.process_frame(TTSStoppedFrame(), FrameDirection.DOWNSTREAM)
        await _emit(user, "on_user_turn_idle")
        assert [f.text for f in s["frames"] if isinstance(f, TTSSpeakFrame)][-1] == s["mod"].vb.IDLE_GOODBYE
        assert isinstance(s["frames"][-1], EndFrame)

    asyncio.run(exercise_call(exercise, VOICE_GREETING="off"))


@pytest.mark.parametrize("failure", ["exception", "rejected", "failure", "cancelled"])
def test_relay_failures_clear_busy_and_use_spoken_guard(exercise_call, monkeypatch, failure):
    from pipecat.frames.frames import TTSSpeakFrame
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator

    async def exercise(s):
        adapter, call = s["adapter"], s["call"]
        user = next(p for p in s["processors"] if isinstance(p, LLMUserAggregator))

        async def handle(event):
            adapter.handled.append(event)
            if failure == "exception":
                raise RuntimeError("sensitive provider detail")
            event._gateway_accepted = failure != "rejected"
            if failure in ("failure", "cancelled"):
                await adapter.on_processing_complete(event, types.SimpleNamespace(value=failure))

        monkeypatch.setattr(adapter, "handle_message", handle)
        await _emit(user, "on_user_turn_stopped", None, types.SimpleNamespace(content="question"))
        await asyncio.gather(*adapter._background_tasks)
        assert not call.pending_events
        if failure != "cancelled":
            assert [f.text for f in s["frames"] if isinstance(f, TTSSpeakFrame)] == [s["mod"].vb.ERROR_REPLY]
        await _emit(user, "on_user_turn_idle")
        assert call.guard.idles == (1 if failure == "cancelled" else 0)

    asyncio.run(exercise_call(exercise))


def test_end_call_bypasses_an_end_frame_stuck_draining(make):
    from pipecat.frames.frames import EndFrame, CancelFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.workers.runner import WorkerRunner
    from pipecat.processors.frame_processor import FrameProcessor

    async def scenario():
        adapter, _ = make()
        draining = asyncio.Event()

        class DisconnectedOutput(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, EndFrame):
                    draining.set()
                    return
                await self.push_frame(frame, direction)

        worker = PipelineWorker(Pipeline([DisconnectedOutput()]), enable_rtvi=False)
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        call = sys.modules[adapter.__class__.__module__]._Call(caller=CALLER, call_sid="draining", task=worker)
        await runner.add_workers(worker)
        running = asyncio.create_task(runner.run())
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(draining.wait(), 2)
        stopping = asyncio.create_task(adapter._end_call(call))
        try:
            done, _ = await asyncio.wait({running}, timeout=0.5)
            assert done, "adapter disconnect is stuck behind the draining EndFrame"
            await stopping
        finally:
            if not running.done():
                await worker.pipeline.queue_frame(CancelFrame())
            await asyncio.wait_for(asyncio.gather(running, stopping, return_exceptions=True), 3)

    asyncio.run(scenario())


def test_interruption_drops_pending_speech_but_not_hermes_work(exercise_call, monkeypatch):
    from pipecat.frames.frames import InterruptionFrame
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
    from pipecat.processors.frame_processor import FrameDirection

    async def exercise(s):
        call, adapter = s["call"], s["adapter"]
        user = next(p for p in s["processors"] if isinstance(p, LLMUserAggregator))
        sink = s["processors"][s["processors"].index(user) + 1]

        async def ignore(*args):
            pass

        monkeypatch.setattr(sink, "push_frame", ignore)
        monkeypatch.setattr(sink, "_start_interruption", ignore)
        await adapter.send(CALLER, "long answer", metadata={"thread_id": call.call_sid, "notify": True})
        call.bot_speaking = True
        adapter._relay_to_hermes(call, "interruption question")
        await adapter.wait_for_messages()
        await sink.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
        assert call.queued_speech == 0 and not call.bot_speaking
        assert call.pending_events
        await _emit(user, "on_user_turn_idle")
        assert call.guard.idles == 0
        await adapter.on_processing_complete(adapter.handled[0], types.SimpleNamespace(value="cancelled"))
        await _emit(user, "on_user_turn_idle")
        assert call.guard.idles == 1

    asyncio.run(exercise_call(exercise))


def test_call_owner_cancellation_waits_for_runner_cleanup(exercise_call, monkeypatch):
    from pipecat.frames.frames import CancelFrame
    from pipecat.services.elevenlabs.stt import ElevenLabsSTTService

    async def scenario():
        ready, released = asyncio.Event(), asyncio.Event()
        seen = {}

        async def exercise(s):
            seen.update(s)

            async def urgent(frame, *args, **kwargs):
                assert isinstance(frame, CancelFrame)
                released.set()

            monkeypatch.setattr(s["worker"].pipeline, "queue_frame", urgent)
            ready.set()
            try:
                await released.wait()
                seen["runner_cleaned"] = True
            except asyncio.CancelledError:
                seen["runner_cancelled"] = True
                raise

        owner = asyncio.create_task(exercise_call(exercise))
        await asyncio.wait_for(ready.wait(), 3)
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owner, 2)
        assert seen.get("runner_cleaned"), "owner cancellation must not cancel the runner before cleanup"
        assert not seen.get("runner_cancelled")
        stt = next(p for p in seen["processors"] if isinstance(p, ElevenLabsSTTService))
        assert stt._session.closed

    asyncio.run(scenario())


def test_greeting_off_arms_the_real_idle_controller(exercise_call):
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
    from pipecat.utils.asyncio.task_manager import TaskManager

    async def exercise(s):
        user = next(p for p in s["processors"] if isinstance(p, LLMUserAggregator))
        controller = user._user_idle_controller
        await controller.setup(TaskManager())
        try:
            await _emit(s["transport"], "on_client_connected", None)
            for frame in s["frames"]:
                await controller.process_frame(frame)
            assert controller._waiting_for_user, "greeting=off must still open an idle-wait window"
            assert controller._idle_timer_task is not None
        finally:
            await controller.cleanup()

    asyncio.run(exercise_call(exercise, VOICE_GREETING="off"))


def test_completion_without_reply_rearms_idle_after_all_pending_turns(exercise_call):
    from pipecat.frames.frames import UserStartedSpeakingFrame, UserStoppedSpeakingFrame
    from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregator
    from pipecat.utils.asyncio.task_manager import TaskManager

    async def exercise(s):
        adapter, call = s["adapter"], s["call"]
        user = next(p for p in s["processors"] if isinstance(p, LLMUserAggregator))
        controller = user._user_idle_controller
        await controller.setup(TaskManager())
        try:
            await controller.process_frame(UserStartedSpeakingFrame())
            await controller.process_frame(UserStoppedSpeakingFrame())
            for text in ("first question", "follow-up question"):
                adapter._relay_to_hermes(call, text)
            await adapter.wait_for_messages()
            first, = adapter.handled
            await adapter.finish_processing(first)
            assert call.pending_events == {f"{call.call_sid}-2"}
            assert not call.idle_enabled
            await adapter.wait_for_messages(2)
            second = adapter.handled[1]
            await adapter.finish_processing(second, "cancelled")
            for frame in s["frames"]:
                await controller.process_frame(frame)
            assert controller._idle_timer_task is not None, "a no-audio result still needs an idle wait"
            assert not call.pending_events and call.idle_enabled
        finally:
            await controller.cleanup()

    asyncio.run(exercise_call(exercise, VOICE_GREETING="off"))


def test_call_time_limit_says_goodbye_then_ends(make, monkeypatch) -> None:
    pytest.importorskip("pipecat")
    adapter, _ = make(VOICE_MAX_CALL_SECONDS="1")
    adapter._max_call_seconds = 0.01
    queued = []

    class FakeTask:
        def has_finished(self):
            return False  # goodbye is still draining

        async def queue_frame(self, frame):
            queued.append(type(frame).__name__)

    task = FakeTask()
    call = sys.modules[adapter.__class__.__module__]._Call(caller=CALLER, call_sid="CA1", task=task)
    adapter._calls[CALLER] = call

    async def run():
        await adapter._enforce_max_duration(call)
        assert (await adapter.send(CALLER, "late Hermes reply")).success is False

    asyncio.run(run())
    assert queued == ["TTSSpeakFrame", "EndFrame"]


# -- outbound callbacks ---------------------------------------------------------

def _sign_url(url: str, params: dict) -> str:
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()


def _post_outbound(client, cb, params, signature=None):
    url = f"{BASE}/voice/outbound?cb={cb}"
    sig = signature if signature is not None else _sign_url(url, params)
    return client.post(
        f"/voice/outbound?cb={cb}", content=urllib.parse.urlencode(params),
        headers={"content-type": "application/x-www-form-urlencoded", "x-twilio-signature": sig})


@pytest.fixture()
def dialing(make, monkeypatch):
    adapter, client = make()
    conn = use_memory_db(monkeypatch)
    callbacks = sys.modules[adapter.__class__.__module__.rsplit(".", 1)[0] + ".callbacks"]
    store = callbacks.CallbackStore(conn)
    cb = store.propose(CALLER, "Confirm tomorrow's appointment", "")
    store.claim_for_dial(cb)
    return adapter, client, cb


OUT = {"To": CALLER, "From": "+15559990000", "CallSid": "CA" + "b" * 32}


def test_answered_callback_opens_a_stream_with_the_approved_purpose(dialing) -> None:
    adapter, client, cb = dialing
    resp = _post_outbound(client, cb, OUT)
    assert resp.status_code == 200 and "<Connect>" in resp.text
    token = resp.text.split('name="token" value="')[1].split('"')[0]
    info = adapter._tokens.redeem(token)
    assert info["caller"] == CALLER and "Confirm tomorrow" in info["opening"]


def test_outbound_webhook_cannot_be_replayed_or_redirected(dialing) -> None:
    adapter, client, cb = dialing
    assert _post_outbound(client, cb, {**OUT, "To": "+15553334444"}).status_code == 403  # wrong number
    assert _post_outbound(client, cb, OUT).status_code == 200
    assert _post_outbound(client, cb, OUT).status_code == 403  # replay


def test_outbound_webhook_needs_a_valid_signature_and_id(dialing) -> None:
    _, client, cb = dialing
    assert _post_outbound(client, cb, OUT, signature="bad").status_code == 403
    assert _post_outbound(client, "../../x", OUT).status_code == 400
    assert _post_outbound(client, "00000000", OUT).status_code == 403  # signed, but no such callback
    assert _post_outbound(client, cb, OUT).status_code == 200  # the real one still works


def test_media_opening_is_kept_on_the_call(make) -> None:
    adapter, _ = make()
    call = sys.modules[adapter.__class__.__module__]._Call(caller=CALLER, call_sid="CA1", opening="hi")
    assert call.opening == "hi"


def test_callee_who_cannot_be_connected_does_not_leave_the_callback_answered(dialing) -> None:
    adapter, client, cb = dialing
    adapter._calls[CALLER] = object()  # callee is already on a live call: admission says busy
    resp = _post_outbound(client, cb, OUT)
    assert "<Connect>" not in resp.text
    callbacks = sys.modules[adapter.__class__.__module__.rsplit(".", 1)[0] + ".callbacks"]
    with callbacks.open_store() as store:
        assert store.get(cb)["status"] == "failed"


def test_opening_message_frames_the_purpose_as_data(dialing) -> None:
    adapter, client, cb = dialing
    token = _post_outbound(client, cb, OUT).text.split('name="token" value="')[1].split('"')[0]
    opening = adapter._tokens.redeem(token)["opening"]
    assert "not an instruction" in opening and '"Confirm tomorrow' in opening
