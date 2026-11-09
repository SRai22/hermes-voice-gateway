"""Voice tool handlers.

Hermes calls a tool handler as ``handler(args: dict, **kwargs)`` and expects a JSON
string back (async handlers are registered with ``is_async=True``). Tool names use
underscores because model APIs reject dots in function names.
"""
from __future__ import annotations

import json
import logging
import sqlite3

from . import twilio_rest
from ._env import secret
from .callbacks import E164, MAX_BRIEF, clean_text, open_store
from .twilio_util import parse_allowed, redact_phone

logger = logging.getLogger(__name__)


def _ok(**fields) -> str:
    return json.dumps({"ok": True, **fields})


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def propose_callback(args: dict, **kwargs) -> str:
    """Record a callback request. It is NOT dialed until an operator approves it."""
    destination = str(args.get("destination", "")).strip()
    brief = clean_text(args.get("brief", ""), MAX_BRIEF)
    staff_member = clean_text(args.get("staff_member", ""), 100)
    if not E164.match(destination):
        return _err("destination must be an E.164 phone number such as +15551234567")
    if not brief:
        return _err("brief is required: say in a sentence why the call is being made")
    if destination not in parse_allowed(secret("VOICE_ALLOWED_USERS")):
        return _err("that number is not on the allowed list, so it cannot be called back")
    try:
        with open_store() as store:
            cb_id = store.propose(destination, brief, staff_member)
    except ValueError as exc:
        return _err(str(exc))
    except sqlite3.Error:
        logger.exception("voice_propose_callback storage error")
        return _err("callback storage is unavailable right now")
    logger.info("voice_propose_callback stored %s for %s", cb_id, redact_phone(destination))
    return _ok(
        callback_id=cb_id,
        status="pending_operator_approval",
        note=f"Nothing will be dialed until an operator runs: /voice-callback approve {cb_id}",
    )


def check_call_status(args: dict, **kwargs) -> str:
    """Report the state of a call we placed for an approved callback (by callback id or its call SID)."""
    call_sid = str(args.get("call_sid", "")).strip()
    cb_id = str(args.get("callback_id", "")).strip().lower()
    if not cb_id and not call_sid:
        return _err("give a callback_id or a call_sid")
    fields: dict = {}
    try:
        with open_store() as store:
            if cb_id:
                row = store.get(cb_id)
                if row is None:
                    return _err(f"no callback {cb_id}")
                fields["callback"] = {"id": row["id"], "status": row["status"], "error": row["error"]}
                call_sid = row["call_sid"]
            elif not store.knows_call(call_sid):
                # The agent may only look at calls this gateway placed, not the whole Twilio account.
                return _err("unknown call: only calls placed for approved callbacks can be looked up")
    except sqlite3.Error:
        logger.exception("voice_check_call_status storage error")
        return _err("callback storage is unavailable right now")
    if not call_sid:
        return _ok(**fields, note="No call has been placed for this callback yet.")
    account_sid, auth_token = secret("TWILIO_ACCOUNT_SID"), secret("TWILIO_AUTH_TOKEN")
    if not (account_sid and auth_token):
        return _err("Twilio credentials are not configured")
    try:
        call = twilio_rest.fetch_call(account_sid, auth_token, call_sid)
    except ValueError as exc:
        return _err(str(exc))
    except twilio_rest.TwilioError as exc:
        return _err(f"Twilio: {exc}")
    # No phone numbers in the result: the model does not need them to report status.
    return _ok(
        call_sid=call_sid,
        status=call.get("status"),
        direction=call.get("direction"),
        duration_seconds=call.get("duration"),
        start_time=call.get("start_time"),
        end_time=call.get("end_time"),
        **fields,
    )


async def start_local_pipeline(args: dict, **kwargs) -> str:
    """Dev only: start the local WebRTC test pipeline (keys come from the environment)."""
    from .pipeline import start_local_pipeline as _start

    return json.dumps(await _start())
