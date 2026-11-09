"""Lifecycle hooks for the voice gateway.

``on_pre_tool_call`` vetoes a voice tool call while the credentials that tool needs
are missing, so Hermes never half-executes a voice action.
"""
from __future__ import annotations

import logging

from ._env import secret
from .pipeline import REQUIRED_ENV as LOCAL_PIPELINE_ENV

logger = logging.getLogger(__name__)

TWILIO_ENV = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
_NEEDS = {"voice_start_local_pipeline": LOCAL_PIPELINE_ENV}


def on_pre_tool_call(tool_name: str = "", args=None, **kwargs):
    """Return ``{"action": "block", "message": ...}`` to veto; ``None`` to allow.

    Fire site contract (this Hermes): a ``{"action": "block", "message"}``
    return replaces the tool call with an error to the caller; anything else
    lets the call proceed.
    """
    if not str(tool_name).startswith("voice_"):
        return None

    missing = [v for v in _NEEDS.get(tool_name, TWILIO_ENV) if not secret(v)]
    if missing:
        message = (
            "hermes-voice-gateway: not configured: missing env "
            + ", ".join(missing)
            + "; set them before calling " + tool_name
        )
        logger.warning("blocking %s: %s", tool_name, message)
        return {"action": "block", "message": message}
    return None
