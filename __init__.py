"""Hermes Voice Gateway plugin: ``register(ctx)`` entry point.

Registers a ``voice`` platform (Twilio phone calls, see ``adapter.py``), two
call tools, the ``/voice-callback`` approval command, a local dev-pipeline tool,
and a ``pre_tool_call`` guard.
Nothing here imports Hermes internals, Pipecat or Twilio at load time.
"""
from __future__ import annotations

import importlib.util
import logging

from ._env import secret
from .hooks import on_pre_tool_call
from .schemas import (
    CHECK_STATUS_SCHEMA,
    LOCAL_PIPELINE_SCHEMA,
    PROPOSE_CALLBACK_SCHEMA,
)
from .callbacks import callback_command
from .tools import check_call_status, propose_callback, start_local_pipeline

logger = logging.getLogger(__name__)

TOOLSET = "voice"
PLATFORM_ENV = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "ELEVENLABS_API_KEY", "VOICE_WEBHOOK_URL")

_PLATFORM_HINT = (
    "You are speaking with a caller on a live phone call. Reply in short, natural spoken "
    "sentences. No markdown, lists, links or emojis. If you need a moment, say so briefly."
)


def _make_adapter(config):
    from .adapter import VoiceAdapter  # imported late: needs Hermes' gateway package

    return VoiceAdapter(config)


def _check_requirements() -> bool:
    """Passive probe: credentials set and the call-server libraries importable."""
    if not all(secret(name) for name in PLATFORM_ENV):
        return False
    return all(importlib.util.find_spec(mod) for mod in ("fastapi", "uvicorn", "pipecat"))


def _env_enablement():
    """Auto-enable the platform when its credentials are present."""
    if not all(secret(name) for name in PLATFORM_ENV):
        return None
    return {"webhook_url": secret("VOICE_WEBHOOK_URL")}


def register(ctx):
    """Register the voice platform, tools and the pre_tool_call guard with Hermes."""
    ctx.register_platform(
        name="voice",
        label="Voice (Twilio calls)",
        adapter_factory=_make_adapter,
        check_fn=_check_requirements,
        required_env=list(PLATFORM_ENV),
        install_hint="pip install 'pipecat-ai[elevenlabs,websocket]' uvicorn",
        env_enablement_fn=_env_enablement,
        allowed_users_env="VOICE_ALLOWED_USERS",
        allow_all_env="VOICE_ALLOW_ALL_USERS",
        allowlist_suppresses_pairing=True,
        max_message_length=1200,
        platform_hint=_PLATFORM_HINT,
        pii_safe=True,
        emoji="📞",
    )
    ctx.register_tool(
        name="voice_propose_callback",
        toolset=TOOLSET,
        schema=PROPOSE_CALLBACK_SCHEMA,
        handler=propose_callback,
        description=(
            "Propose a phone callback. It is only recorded: a human operator must approve "
            "it with /voice-callback before anything is dialed."
        ),
    )
    ctx.register_tool(
        name="voice_check_call_status",
        toolset=TOOLSET,
        schema=CHECK_STATUS_SCHEMA,
        handler=check_call_status,
        description="Look up a call's status from Twilio, by call SID or callback id.",
    )
    ctx.register_tool(
        name="voice_start_local_pipeline",
        toolset=TOOLSET,
        schema=LOCAL_PIPELINE_SCHEMA,
        handler=start_local_pipeline,
        is_async=True,
        description=(
            "Dev only: start a local Pipecat voice pipeline with WebRTC transport "
            "(browser mic, no phone line)."
        ),
    )
    ctx.register_command(
        "voice-callback",
        callback_command,
        description="Review, approve or deny callbacks the agent proposed (nothing dials until approved).",
        args_hint="list | show <id> | approve <id> | deny <id>",
    )
    ctx.register_hook("pre_tool_call", on_pre_tool_call)

    logger.info("hermes-voice-gateway registered (voice platform, 3 tools, 1 command, 1 hook)")
