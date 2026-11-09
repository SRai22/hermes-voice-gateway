"""Lifecycle hooks for the voice gateway.

``on_pre_tool_call`` vetoes any voice tool call while the gateway's provider
credentials are missing, so Hermes never half-executes a voice action.
"""
from __future__ import annotations

import logging
import os

from .pipeline import REQUIRED_ENV

logger = logging.getLogger(__name__)


def on_pre_tool_call(tool_name: str = "", args=None, **kwargs):
    """Return ``{"action": "block", "message": ...}`` to veto; ``None`` to allow.

    Fire site contract (this Hermes): a ``{"action": "block", "message"}``
    return replaces the tool call with an error to the caller; anything else
    lets the call proceed.
    """
    if not str(tool_name).startswith("voice."):
        return None

    missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        message = (
            "hermes-voice-gateway: not configured — missing env "
            + ", ".join(missing)
            + "; set them before calling " + tool_name
        )
        logger.warning("blocking %s: %s", tool_name, message)
        return {"action": "block", "message": message}

    # TODO: when a voice tool carries a Twilio signature, verify it here and
    # block on mismatch instead of passing the call through.
    return None
