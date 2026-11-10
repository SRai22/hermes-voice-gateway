"""Call behaviour shared by the local pipeline and Twilio adapter: greeting, voice profile, guardrails.

Plain Python, no Pipecat import, so the plugin still loads without it.
"""
from __future__ import annotations

import os
from pathlib import Path

try:  # plugin package under Hermes
    from ._env import secret
except ImportError:  # imported top-level by the dev scripts and tests
    from _env import secret

DEFAULT_GREETING = "Hi, how can I help you today?"

# Used when no profile file exists. A profile file replaces it entirely.
DEFAULT_PROFILE = (
    "You are a helpful assistant on a live voice call. "
    "Reply in one or two short spoken sentences. Ask one question at a time. "
    "Never use emojis, lists, markdown, or anything that cannot be read aloud. "
    "If you did not understand the caller, say so briefly and ask them to repeat."
)
MAX_PROFILE_CHARS = 6000  # the profile is sent with every LLM request

# Spoken by the guardrails. Fixed text on purpose: it must work when the LLM does not.
ERROR_REPLY = "Sorry, something went wrong on my end. Could you say that again?"
ERROR_GOODBYE = "I'm having technical trouble, so I'll end the call now. Please call back shortly. Goodbye."
IDLE_PROMPT = "Are you still there?"
IDLE_GOODBYE = "I haven't heard anything, so I'll hang up now. Goodbye."
NOT_UNDERSTOOD = "Sorry, I didn't catch that. Could you say it again?"


def greeting() -> str:
    """Opening line, from VOICE_GREETING. "off" or "none" means no greeting."""
    text = secret("VOICE_GREETING", DEFAULT_GREETING).strip()
    return "" if text.lower() in ("off", "none") else text


def profile_path() -> Path:
    """VOICE_PROFILE_PATH, else <HERMES_HOME or ~/.hermes>/voice/PROFILE.md."""
    explicit = secret("VOICE_PROFILE_PATH").strip()
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home) / "voice" / "PROFILE.md"


def load_profile() -> str:
    """System prompt for voice calls: the profile file if it has text, else DEFAULT_PROFILE."""
    try:
        text = profile_path().read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_PROFILE
    return text[:MAX_PROFILE_CHARS] or DEFAULT_PROFILE


class CallGuard:
    """Counts trouble in one call and says what to do. Returns (text to speak, hang up afterwards)."""

    # shortcut: errors are counted per call, never reset; upgrade if long calls hit transient errors.
    def __init__(self, max_errors: int = 3, max_idles: int = 2, max_empty: int = 2) -> None:
        self.max_errors, self.max_idles, self.max_empty = max_errors, max_idles, max_empty
        self.errors = self.idles = self.empty = 0

    def on_error(self) -> tuple[str, bool]:
        self.errors += 1
        return (ERROR_GOODBYE, True) if self.errors >= self.max_errors else (ERROR_REPLY, False)

    def on_idle(self) -> tuple[str, bool]:
        self.idles += 1
        return (IDLE_GOODBYE, True) if self.idles >= self.max_idles else (IDLE_PROMPT, False)

    def on_turn(self, text: str | None) -> str | None:
        """Caller's turn ended. Returns "say it again" text for a turn with no words, else None."""
        self.idles = 0
        if (text or "").strip():
            self.empty = 0
            return None
        self.empty += 1
        return NOT_UNDERSTOOD if self.empty <= self.max_empty else None
