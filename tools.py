"""Voice tool handlers (scaffold).

The Twilio-backed tools are not implemented yet. They return ``ok: False`` so
Hermes never tells a caller that an action happened when it did not. Provider
credentials are checked once, by the ``pre_tool_call`` hook in ``hooks.py``.
Providers are only imported at call time, so the plugin loads without them.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _not_implemented(tool: str) -> dict:
    return {"ok": False, "error": f"{tool} is not implemented yet (scaffold); nothing was done."}


def propose_callback(**kwargs) -> dict:
    """Propose a callback for operator approval before the gateway dials out."""
    # TODO: persist (destination, brief, staff_member) with status=pending and
    # surface it in the operator console for approval. Do not log the number or brief.
    logger.info("voice.propose_callback called")
    return _not_implemented("voice.propose_callback")


def check_call_status(call_sid: str = "", **kwargs) -> dict:
    """Read the status of a voice call from the provider."""
    # TODO: fetch the call status from Twilio for call_sid.
    logger.info("voice.check_call_status call_sid=%s", call_sid)
    return _not_implemented("voice.check_call_status")
