"""Smoke test for the hermes-voice-gateway plugin scaffold.

Loads the plugin ``__init__.py`` exactly the way Hermes does
(:func:`hermes_cli.plugin_host_child._import_plugin` — by file path under an
assigned package name) and runs ``register(ctx)`` against a stand-in
:class:`hermes_cli.plugins.PluginContext`. No Hermes runtime or provider SDK
is required.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_plugin_init():
    """Mirror Hermes' loader: exec plugin_dir/__init__.py under a package name
    whose __path__ is the plugin dir, so ``from .schemas import ...`` resolves."""
    init_file = ROOT / "__init__.py"
    module_name = "hermes_voice_gateway_scaffold"
    spec = importlib.util.spec_from_file_location(
        module_name, init_file, submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(ROOT)]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class FakeCtx:
    """Stand-in for hermes_cli.plugins.PluginContext."""

    def __init__(self) -> None:
        self.tools = []
        self.hooks = []

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools.append((name, toolset, handler.__name__))

    def register_hook(self, hook_name, callback, **kwargs):
        self.hooks.append((hook_name, callback.__name__))


def test_register_wires_tools_and_hook(monkeypatch) -> None:
    for var in ("ELEVENLABS_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    mod = _load_plugin_init()
    ctx = FakeCtx()
    mod.register(ctx)

    names = [tool[0] for tool in ctx.tools]
    for name in (
        "voice.route_inbound",
        "voice.propose_callback",
        "voice.check_call_status",
        "voice.start_local_pipeline",
    ):
        assert name in names, names

    assert ctx.hooks == [("pre_tool_call", "on_pre_tool_call")], ctx.hooks

    # Keys missing: voice tools are blocked, other tools pass.
    assert mod.on_pre_tool_call(tool_name="voice.route_inbound")["action"] == "block"
    assert mod.on_pre_tool_call(tool_name="voice.start_local_pipeline")["action"] == "block"
    assert mod.on_pre_tool_call(tool_name="files.read") is None


def test_hook_allows_voice_tools_when_keys_set(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "y")
    mod = _load_plugin_init()
    assert mod.on_pre_tool_call(tool_name="voice.route_inbound") is None


def test_stub_tools_do_not_claim_success() -> None:
    mod = _load_plugin_init()
    assert mod.route_inbound(call_sid="CA1")["ok"] is False
    assert mod.propose_callback(destination="x", brief="y")["ok"] is False
    assert mod.check_call_status(call_sid="CA1")["ok"] is False


def test_local_pipeline_schema_takes_no_secrets() -> None:
    mod = _load_plugin_init()
    assert mod.LOCAL_PIPELINE_SCHEMA["properties"] == {}
    assert "required" not in mod.LOCAL_PIPELINE_SCHEMA
