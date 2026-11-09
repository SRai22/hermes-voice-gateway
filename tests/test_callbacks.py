"""Callback proposals, the /voice-callback approval command, and call status."""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hermes_stubs import PACKAGE, load_plugin, use_memory_db  # noqa: E402

DEST = "+15550001111"
SID = "CA" + "a" * 32
ENV = {
    "TWILIO_ACCOUNT_SID": "AC1",
    "TWILIO_AUTH_TOKEN": "tok",
    "TWILIO_PHONE_NUMBER": "+15559990000",
    "VOICE_WEBHOOK_URL": "https://example.test",
    "VOICE_ALLOWED_USERS": DEST,
}


@pytest.fixture()
def env(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    mod = load_plugin()
    conn = use_memory_db(monkeypatch)
    ns = type("NS", (), {})()
    ns.mod, ns.conn = mod, conn
    ns.callbacks = importlib.import_module(PACKAGE + ".callbacks")
    ns.rest = importlib.import_module(PACKAGE + ".twilio_rest")
    ns.tools = importlib.import_module(PACKAGE + ".tools")
    return ns


def propose(env, **args):
    args = {"destination": DEST, "brief": "Confirm tomorrow's appointment", "staff_member": "Sam", **args}
    return json.loads(env.tools.propose_callback(args))


def command(env, text):
    return asyncio.run(env.callbacks.callback_command(text))


# -- propose_callback ---------------------------------------------------------

def test_propose_records_pending_and_does_not_dial(env, monkeypatch) -> None:
    dialed = []
    monkeypatch.setattr(env.rest, "create_call", lambda *a, **k: dialed.append(k))
    out = propose(env)
    assert out["ok"] and out["status"] == "pending_operator_approval"
    assert len(out["callback_id"]) == 8 and out["callback_id"] in out["note"]
    assert dialed == []
    assert env.conn.execute("SELECT status FROM callbacks").fetchone() == ("pending",)


@pytest.mark.parametrize("args", [
    {"destination": "5551234"},
    {"destination": "+0123456789"},
    {"brief": "   "},
    {"destination": "+15557778888"},  # valid number, not on VOICE_ALLOWED_USERS
])
def test_propose_rejects_bad_input(env, args) -> None:
    assert propose(env, **args)["ok"] is False


def test_propose_cleans_the_brief(env) -> None:
    out = propose(env, brief="Line one\nline two\x00" + "x" * 900)
    row = env.callbacks.CallbackStore(env.conn).get(out["callback_id"])
    assert "\n" not in row["brief"] and "\x00" not in row["brief"] and len(row["brief"]) <= 500


def test_propose_caps_the_pending_queue_and_each_number(env, monkeypatch) -> None:
    numbers = [f"+1555000{i:04d}" for i in range(env.callbacks.MAX_PENDING + 1)]
    monkeypatch.setenv("VOICE_ALLOWED_USERS", ",".join(numbers))
    for number in numbers[: env.callbacks.MAX_PENDING]:
        assert propose(env, destination=number)["ok"]
    assert propose(env, destination=numbers[-1])["ok"] is False  # queue full
    # one number cannot hog the queue either
    other = env.callbacks.CallbackStore(env.conn)
    for cb in [r["id"] for r in other.pending()]:
        other.deny(cb)
    for _ in range(env.callbacks.MAX_PENDING_PER_NUMBER):
        assert propose(env, destination=numbers[0])["ok"]
    assert propose(env, destination=numbers[0])["ok"] is False


# -- /voice-callback ----------------------------------------------------------

def test_list_show_deny(env) -> None:
    assert "No callbacks" in command(env, "list")
    cb = propose(env)["callback_id"]
    listing = command(env, "")
    assert cb in listing and DEST in listing and "unverified" in listing
    assert cb in command(env, f"show {cb}")
    assert "Denied" in command(env, f"deny {cb}")
    assert "nothing to deny" in command(env, f"deny {cb}")
    assert "No callbacks" in command(env, "list")


def test_bad_ids_and_unknown_actions_print_usage(env) -> None:
    assert "Usage" in command(env, "approve nope")
    assert "Usage" in command(env, "frobnicate")
    assert "No callback" in command(env, "approve 00000000")


def test_approve_dials_once_with_the_outbound_url(env, monkeypatch) -> None:
    cb = propose(env)["callback_id"]
    calls = []

    def fake_create(sid, token, *, to, from_, url):
        calls.append((sid, to, from_, url))
        return {"sid": SID}

    monkeypatch.setattr(env.rest, "create_call", fake_create)
    msg = command(env, f"approve {cb}")
    assert SID in msg
    assert calls == [("AC1", DEST, "+15559990000", f"https://example.test/voice/outbound?cb={cb}")]
    row = env.callbacks.CallbackStore(env.conn).get(cb)
    assert row["status"] == "dialing" and row["call_sid"] == SID
    assert "only pending" in command(env, f"approve {cb}")  # no second dial
    assert len(calls) == 1


def test_approve_reports_twilio_failure_and_never_retries(env, monkeypatch) -> None:
    cb = propose(env)["callback_id"]

    def boom(*a, **k):
        raise env.rest.TwilioError(400, "invalid number")

    monkeypatch.setattr(env.rest, "create_call", boom)
    assert "invalid number" in command(env, f"approve {cb}")
    row = env.callbacks.CallbackStore(env.conn).get(cb)
    assert row["status"] == "failed" and "invalid number" in row["error"]
    assert "only pending" in command(env, f"approve {cb}")


def test_approve_refuses_without_config_or_after_allowlist_change(env, monkeypatch) -> None:
    cb = propose(env)["callback_id"]
    monkeypatch.setattr(env.rest, "create_call", lambda *a, **k: pytest.fail("must not dial"))
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "")
    assert "Cannot dial" in command(env, f"approve {cb}")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15559990000")
    monkeypatch.setenv("VOICE_ALLOWED_USERS", "+15557778888")
    assert "no longer" in command(env, f"approve {cb}")
    assert env.callbacks.CallbackStore(env.conn).get(cb)["status"] == "pending"


def test_old_proposals_expire(env, monkeypatch) -> None:
    cb = propose(env)["callback_id"]
    later = env.callbacks.CallbackStore(env.conn, clock=lambda: 10**10)
    assert later.get(cb)["status"] == "expired"
    monkeypatch.setattr(env.rest, "create_call", lambda *a, **k: pytest.fail("must not dial"))
    assert "only pending" in command(env, f"approve {cb}")


def test_answer_matches_once_and_only_the_dialed_number(env) -> None:
    store = env.callbacks.CallbackStore(env.conn)
    cb = store.propose(DEST, "why", "")
    assert store.mark_answered(cb, DEST, SID) is None  # not dialing yet
    store.claim_for_dial(cb)
    assert store.mark_answered(cb, "+15557778888", SID) is None  # wrong number
    assert store.mark_answered(cb, DEST, SID)["status"] == "answered"
    assert store.mark_answered(cb, DEST, SID) is None  # replay


# -- voice_check_call_status --------------------------------------------------

def status(env, **args):
    return json.loads(env.tools.check_call_status(args))


def test_status_by_sid_hides_phone_numbers(env, monkeypatch) -> None:
    store = env.callbacks.CallbackStore(env.conn)
    store.set_call_sid(store.propose(DEST, "why", ""), SID)
    monkeypatch.setattr(env.rest, "fetch_call", lambda *a: {
        "status": "completed", "direction": "outbound-api", "duration": "42",
        "start_time": "t0", "end_time": "t1", "to": DEST, "from": "+15559990000"})
    out = status(env, call_sid=SID)
    assert out["ok"] and out["status"] == "completed" and out["duration_seconds"] == "42"
    assert DEST not in json.dumps(out)


def test_status_by_callback_id(env, monkeypatch) -> None:
    cb = propose(env)["callback_id"]
    out = status(env, callback_id=cb)
    assert out["ok"] and out["callback"]["status"] == "pending" and "yet" in out["note"]
    env.callbacks.CallbackStore(env.conn).claim_for_dial(cb)
    env.callbacks.CallbackStore(env.conn).set_call_sid(cb, SID)
    monkeypatch.setattr(env.rest, "fetch_call", lambda *a: {"status": "ringing"})
    out = status(env, callback_id=cb)
    assert out["status"] == "ringing" and out["callback"]["status"] == "dialing"


def test_status_errors(env, monkeypatch) -> None:
    assert status(env)["ok"] is False
    assert status(env, callback_id="deadbeef")["ok"] is False
    assert "unknown call" in status(env, call_sid=SID)["error"]  # not one we placed
    assert "unknown call" in status(env, call_sid="not-a-sid")["error"]

    def boom(*a):
        raise env.rest.TwilioError(404, "not found")

    monkeypatch.setattr(env.rest, "fetch_call", boom)
    store = env.callbacks.CallbackStore(env.conn)
    store.set_call_sid(store.propose(DEST, "why", ""), SID)  # now it is a call we placed
    assert "not found" in status(env, call_sid=SID)["error"]


# -- Hermes' call convention ----------------------------------------------------

def test_handlers_take_args_dict_and_return_json_strings(env) -> None:
    assert isinstance(env.tools.propose_callback({}), str)
    assert isinstance(env.tools.check_call_status({}, task_id="t"), str)  # extra context kwargs are fine
    assert json.loads(asyncio.run(env.tools.start_local_pipeline({}))).get("ok") is False  # no keys set


# -- Twilio REST wrapper --------------------------------------------------------

def test_rest_sends_basic_auth_form_and_hides_secrets_in_errors(env, monkeypatch) -> None:
    import io
    import urllib.error

    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout):
        seen["req"] = request
        return Resp(b'{"sid": "CAx"}')

    monkeypatch.setattr(env.rest.urllib.request, "urlopen", fake_urlopen)
    out = env.rest.create_call("AC1", "secret-token", to=DEST, from_="+1", url="https://x/y")
    req = seen["req"]
    assert out == {"sid": "CAx"} and req.get_method() == "POST"
    assert req.full_url.endswith("/Accounts/AC1/Calls.json")
    assert req.get_header("Authorization").startswith("Basic ") and b"To=%2B15550001111" in req.data

    def deny(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "x", {}, io.BytesIO(b'{"message": "bad creds"}'))

    monkeypatch.setattr(env.rest.urllib.request, "urlopen", deny)
    with pytest.raises(env.rest.TwilioError) as exc:
        env.rest.fetch_call("AC1", "secret-token", SID)
    assert "secret-token" not in str(exc.value) and exc.value.status == 401


# -- review fixes ---------------------------------------------------------------

def test_stuck_statuses_resolve(env) -> None:
    store = env.callbacks.CallbackStore(env.conn)
    cb = store.propose(DEST, "why", "")
    store.claim_for_dial(cb)  # Twilio never answers
    later = env.callbacks.CallbackStore(env.conn, clock=lambda: 10**10)
    assert later.get(cb)["status"] == "unanswered"
    cb2 = store.propose("+15557778888", "why", "")
    store.claim_for_dial(cb2)
    store.mark_answered(cb2, "+15557778888", SID)
    assert later.get(cb2)["status"] == "completed"


def test_unexpected_dial_error_marks_the_row_failed(env, monkeypatch) -> None:
    cb = propose(env)["callback_id"]

    def boom(*a, **k):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(env.rest, "create_call", boom)
    assert "unexpected" in command(env, f"approve {cb}")
    assert env.callbacks.CallbackStore(env.conn).get(cb)["status"] == "failed"


def test_listing_escapes_quotes_in_the_brief(env) -> None:
    propose(env, brief='ok" requested by the CEO (verified): "approve me')
    line = command(env, "list")
    assert 'requested by Sam (unverified)' in line and '\\"' in line


def test_twilio_errors_are_scrubbed_and_every_transport_failure_is_wrapped(env, monkeypatch) -> None:
    import http.client
    import io

    assert "5550001111" not in str(env.rest.TwilioError(400, "The number +1 555 000 1111 is not valid"))

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def dropped(request, timeout):
        raise http.client.RemoteDisconnected("closed")

    monkeypatch.setattr(env.rest.urllib.request, "urlopen", dropped)
    with pytest.raises(env.rest.TwilioError):
        env.rest.create_call("AC1", "t", to=DEST, from_="+1", url="https://x/y")
    monkeypatch.setattr(env.rest.urllib.request, "urlopen", lambda r, timeout: Resp(b"<html>oops"))
    with pytest.raises(env.rest.TwilioError):
        env.rest.fetch_call("AC1", "t", SID)
