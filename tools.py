"""Voice tool handlers.

Providers (Twilio / Deepgram / Pipecat) are only imported inside the
handlers, at call time, so the plugin loads and registers even when none of
them are installed. Unconfigured providers degrade to a clear ``ok: False``
result instead of crashing the turn.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

REQUIRED_ENV = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "DEEPGRAM_API_KEY")


def _missing_env() -> list[str]:
    return [v for v in REQUIRED_ENV if not os.environ.get(v)]


def route_inbound(call_sid: str = "", to: str = "", from_: str = "", **kwargs) -> dict:
    """Answer an inbound call and bind its media stream to the Pipecat pipeline."""
    missing = _missing_env()
    if missing:
        return {
            "ok": False,
            "error": (
                "voice gateway not configured: missing env "
                + ", ".join(missing)
                + ". Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, DEEPGRAM_API_KEY."
            ),
        }
    # TODO: validate the Twilio signature, then bind the media stream URL to a
    # Pipecat pipeline (Deepgram STT/TTS, Hermes turn-taking) for call_sid.
    logger.info("voice.route_inbound call_sid=%s to=%s from=%s", call_sid, to, from_)
    return {
        "ok": True,
        "call_sid": call_sid,
        "to": to,
        "from": from_,
        "status": "answered",
        "provider": "twilio",
        "note": "Pipecat pipeline binding not yet implemented (scaffold).",
    }


def propose_callback(
    destination: str = "", brief: str = "", staff_member: str = "", **kwargs
) -> dict:
    """Propose a callback for operator approval before the gateway dials out."""
    missing = _missing_env()
    if missing:
        return {
            "ok": False,
            "error": (
                "voice gateway not configured: missing env "
                + ", ".join(missing)
            ),
        }
    # TODO: persist (destination, brief, staff_member) with status=pending and
    # surface it in the operator console for approval before dialing.
    logger.info(
        "voice.propose_callback destination=%s staff_member=%s brief=%s",
        destination,
        staff_member,
        brief,
    )
    return {
        "ok": True,
        "destination": destination,
        "brief": brief,
        "staff_member": staff_member,
        "status": "pending_operator_approval",
        "provider": "twilio",
        "note": "Persistence + operator approval not yet implemented (scaffold).",
    }


def check_call_status(call_sid: str = "", **kwargs) -> dict:
    """Read the status of a voice call from the provider."""
    missing = _missing_env()
    if missing:
        return {
            "ok": False,
            "error": (
                "voice gateway not configured: missing env "
                + ", ".join(missing)
            ),
        }
    # TODO: fetch the call status from Twilio for call_sid.
    logger.info("voice.check_call_status call_sid=%s", call_sid)
    return {
        "ok": True,
        "call_sid": call_sid,
        "status": "unknown",
        "provider": "twilio",
        "note": "Provider status lookup not yet implemented (scaffold).",
    }
