"""Lifecycle hooks for the voice gateway.

``on_pre_tool_call`` restricts callers to their live inventory/enquiry tools and
checks credentials for operator-side voice tools. Permission errors deny access.
"""
from __future__ import annotations

import logging
from contextlib import suppress

from . import caller_tools
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
    # Hermes fails open when a hook raises. Default to denial if identity or
    # permission lookup itself breaks; never leak exception details to callers.
    voice_context = None
    with suppress(Exception):
        voice_context = caller_tools.is_voice_context()
    if voice_context is None:
        return {"action": "block", "message": "Caller identity validation is unavailable."}
    if voice_context or tool_name in caller_tools.TOOL_NAMES:
        if tool_name not in caller_tools.TOOL_NAMES:
            return {"action": "block", "message": "Phone callers may only search inventory and save their own enquiry."}
        with suppress(Exception):
            caller_tools.require_access()
            return None
        return {"action": "block", "message": "A currently authorized phone turn is required for this tool."}
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
