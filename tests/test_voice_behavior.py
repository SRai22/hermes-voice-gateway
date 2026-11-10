"""Greeting, voice profile and guardrail logic (no Pipecat needed)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import voice_behavior as vb  # noqa: E402


def test_greeting_default_custom_and_off(monkeypatch) -> None:
    monkeypatch.delenv("VOICE_GREETING", raising=False)
    assert vb.greeting() == vb.DEFAULT_GREETING
    monkeypatch.setenv("VOICE_GREETING", "Thanks for calling the clinic.")
    assert vb.greeting() == "Thanks for calling the clinic."
    monkeypatch.setenv("VOICE_GREETING", "OFF")
    assert vb.greeting() == ""


def test_profile_file_replaces_default_and_is_capped(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VOICE_PROFILE_PATH", str(tmp_path / "PROFILE.md"))
    assert vb.load_profile() == vb.DEFAULT_PROFILE  # missing file
    (tmp_path / "PROFILE.md").write_text("   \n", encoding="utf-8")
    assert vb.load_profile() == vb.DEFAULT_PROFILE  # blank file
    (tmp_path / "PROFILE.md").write_text("Be brief.", encoding="utf-8")
    assert vb.load_profile() == "Be brief."
    (tmp_path / "PROFILE.md").write_text("x" * (vb.MAX_PROFILE_CHARS + 50), encoding="utf-8")
    assert len(vb.load_profile()) == vb.MAX_PROFILE_CHARS


def test_profile_default_location_uses_hermes_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("VOICE_PROFILE_PATH", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert vb.profile_path() == tmp_path / "voice" / "PROFILE.md"


def test_guard_errors_apologise_then_hang_up() -> None:
    guard = vb.CallGuard(max_errors=3)
    assert guard.on_error() == (vb.ERROR_REPLY, False)
    assert guard.on_error() == (vb.ERROR_REPLY, False)
    assert guard.on_error() == (vb.ERROR_GOODBYE, True)


def test_guard_idle_prompts_then_hangs_up() -> None:
    guard = vb.CallGuard(max_idles=2)
    assert guard.on_idle() == (vb.IDLE_PROMPT, False)
    assert guard.on_idle() == (vb.IDLE_GOODBYE, True)


def test_guard_empty_turn_asks_twice_then_stays_quiet_and_words_reset() -> None:
    guard = vb.CallGuard(max_empty=2)
    assert guard.on_turn("") == vb.NOT_UNDERSTOOD
    assert guard.on_turn("  ") == vb.NOT_UNDERSTOOD
    assert guard.on_turn(None) is None
    assert guard.on_turn("hello") is None  # words reset the count
    assert guard.on_turn("") == vb.NOT_UNDERSTOOD


def test_guard_turn_resets_idle_count() -> None:
    guard = vb.CallGuard(max_idles=2)
    guard.on_idle()
    guard.on_turn("hi")
    assert guard.on_idle() == (vb.IDLE_PROMPT, False)
