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

from _hermes_stubs import load_adapter_module  # noqa: E402

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
    result = asyncio.run(adapter.send(CALLER, "  Hi there  "))
    assert result.success and queued == [("speak", "Hi there")]
    assert asyncio.run(adapter.send("+19990000000", "x")).success is False
    adapter._calls[CALLER].task.finished = True  # caller hung up
    assert asyncio.run(adapter.send(CALLER, "too late")).success is False
    assert queued == [("speak", "Hi there")]


def test_transcript_becomes_a_hermes_text_message(make) -> None:
    adapter, _ = make()
    call = sys.modules[adapter.__class__.__module__]._Call(caller=CALLER, call_sid="CA1")

    async def run():
        adapter._relay_to_hermes(call, "what are your hours")
        await asyncio.gather(*adapter._background_tasks)

    asyncio.run(run())
    (event,) = adapter.handled
    assert event.text == "what are your hours"
    assert event.message_type == "text"  # TEXT so Hermes' own auto-TTS does not double-speak
    assert event.source.chat_id == CALLER
    assert event.message_id == "CA1-1"
    assert event.allow_gateway_control is False  # spoken words must not run /commands


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
