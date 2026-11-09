"""Hermes Voice Gateway plugin — ``register(ctx)`` entry point.

Wires three voice tools and a ``pre_tool_call`` hook that vetoes voice
tool calls while the gateway's provider credentials are missing.
"""
from __future__ import annotations

import logging

from .hooks import on_pre_tool_call
from .pipeline import start_local_pipeline as _pipeline_start  # noqa: F401
from .schemas import (
    CHECK_STATUS_SCHEMA,
    LOCAL_PIPELINE_SCHEMA,
    PROPOSE_CALLBACK_SCHEMA,
    ROUTE_INBOUND_SCHEMA,
)
from .tools import (
    check_call_status,
    propose_callback,
    route_inbound,
    start_local_pipeline,
)

logger = logging.getLogger(__name__)

TOOLSET = "voice"


def register(ctx):
    """Register voice tools and the pre_tool_call guard with Hermes.

    ``ctx`` is the :class:`hermes_cli.plugins.PluginContext` Hermes hands each
    loaded plugin. Nothing here touches the network — providers are only used
    inside the tool handlers, at call time.
    """
    ctx.register_tool(
        name="voice.route_inbound",
        toolset=TOOLSET,
        schema=ROUTE_INBOUND_SCHEMA,
        handler=route_inbound,
        description=(
            "Answer an inbound call on the business line and start the "
            "Pipecat voice pipeline (Twilio media stream -> Deepgram STT/TTS)."
        ),
    )
    ctx.register_tool(
        name="voice.propose_callback",
        toolset=TOOLSET,
        schema=PROPOSE_CALLBACK_SCHEMA,
        handler=propose_callback,
        description=(
            "Propose a callback for operator approval before the gateway "
            "dials out (destination + brief + requesting staff member)."
        ),
    )
    ctx.register_tool(
        name="voice.check_call_status",
        toolset=TOOLSET,
        schema=CHECK_STATUS_SCHEMA,
        handler=check_call_status,
        description="Read the status of a voice call from the provider (Twilio).",
    )
    ctx.register_tool(
        name="voice.start_local_pipeline",
        toolset=TOOLSET,
        schema=LOCAL_PIPELINE_SCHEMA,
        handler=start_local_pipeline,
        is_async=True,
        description=(
            "Start a Pipecat voice pipeline locally with smallwebrtc "
            "transport (no Twilio). Used for development and testing."
        ),
    )
    ctx.register_hook("pre_tool_call", on_pre_tool_call)

    logger.info(
        "hermes-voice-gateway registered (%d tools, 1 hook) under toolset %r",
        4,
        TOOLSET,
    )
