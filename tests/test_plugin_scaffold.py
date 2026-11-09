"""Plugin wiring: tools, hook, platform registration. Mirrors Hermes' loader."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hermes_stubs import load_plugin  # noqa: E402


class FakeCtx:
    def __init__(self) -> None:
        self.tools, self.hooks, self.platforms, self.commands = [], [], [], []

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools.append((name, toolset, handler.__name__))

    def register_hook(self, hook_name, callback, **kwargs):
        self.hooks.append((hook_name, callback.__name__))

    def register_platform(self, **kwargs):
        self.platforms.append(kwargs)

    def register_command(self, name, handler, **kwargs):
        self.commands.append((name, handler.__name__))


def test_register_wires_platform_tools_and_hook(monkeypatch) -> None:
    mod = load_plugin()
    ctx = FakeCtx()
    mod.register(ctx)

    assert all("." not in t[0] for t in ctx.tools)  # model APIs reject dots in tool names
    assert sorted(t[0] for t in ctx.tools) == [
        "voice_check_call_status",
        "voice_propose_callback",
        "voice_start_local_pipeline",
    ]
    assert ctx.hooks == [("pre_tool_call", "on_pre_tool_call")]
    assert ctx.commands == [("voice-callback", "callback_command")]
    assert not any("approve" in t[0] for t in ctx.tools)  # approval is human-only, never a tool
    (platform,) = ctx.platforms
    assert platform["name"] == "voice"
    assert platform["allowed_users_env"] == "VOICE_ALLOWED_USERS"
    assert "VOICE_WEBHOOK_URL" in platform["required_env"]


def test_platform_factory_is_lazy_and_requirements_probe_is_passive(monkeypatch) -> None:
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "ELEVENLABS_API_KEY", "VOICE_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)
    mod = load_plugin()
    assert mod._check_requirements() is False
    assert mod._env_enablement() is None
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "ELEVENLABS_API_KEY"):
        monkeypatch.setenv(var, "x")
    monkeypatch.setenv("VOICE_WEBHOOK_URL", "https://example.test")
    assert mod._env_enablement() == {"webhook_url": "https://example.test"}


def test_hook_blocks_tools_missing_their_env_and_allows_others(monkeypatch) -> None:
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "ELEVENLABS_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    mod = load_plugin()
    assert mod.on_pre_tool_call(tool_name="voice_check_call_status")["action"] == "block"
    assert mod.on_pre_tool_call(tool_name="voice_start_local_pipeline")["action"] == "block"
    assert mod.on_pre_tool_call(tool_name="files.read") is None

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "a")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "b")
    assert mod.on_pre_tool_call(tool_name="voice_check_call_status") is None
    # the dev pipeline needs its own keys, not Twilio's
    assert mod.on_pre_tool_call(tool_name="voice_start_local_pipeline")["action"] == "block"


def test_local_pipeline_schema_takes_no_secrets() -> None:
    mod = load_plugin()
    assert mod.LOCAL_PIPELINE_SCHEMA["properties"] == {}
