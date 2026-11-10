"""Stand-ins for Hermes' ``gateway`` package, and a loader that mirrors how Hermes
imports the plugin (by file path, under an assigned package name)."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "hermes_voice_gateway_under_test"


@dataclass
class SendResult:
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None


@dataclass
class MessageEvent:
    text: str
    message_type: Any = None
    source: Any = None
    message_id: Optional[str] = None
    allow_gateway_control: bool = True
    channel_prompt: Optional[str] = None


class FakeBase:
    def __init__(self, config, platform):
        self.config, self.platform = config, platform
        self._background_tasks: set = set()
        self.handled: list = []
        self.fatal: Optional[tuple] = None

    def build_source(self, **kw):
        return SimpleNamespace(**kw)

    async def handle_message(self, event):
        self.handled.append(event)

    def prepare_tts_text(self, text):
        return text.strip()

    def _set_fatal_error(self, code, msg, retryable=False):
        self.fatal = (code, msg, retryable)

    def _mark_connected(self):
        self.connected = True

    def _mark_disconnected(self):
        self.connected = False


def install_gateway_stubs() -> None:
    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    mod("gateway")
    mod("gateway.config", Platform=lambda name: name, PlatformConfig=object)
    mod("gateway.platforms")
    mod("gateway.platforms.base", BasePlatformAdapter=FakeBase, SendResult=SendResult)
    mod("gateway.platforms.event", MessageEvent=MessageEvent, MessageType=SimpleNamespace(TEXT="text", VOICE="voice"))


def load_plugin():
    """Load the plugin package fresh (so env read in __init__ is current)."""
    install_gateway_stubs()
    for name in [n for n in sys.modules if n == PACKAGE or n.startswith(PACKAGE + ".")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = PACKAGE
    module.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
    return module


def load_adapter_module():
    load_plugin()
    return importlib.import_module(PACKAGE + ".adapter")


def use_memory_db(monkeypatch):
    """Point the callback store at one shared in-memory SQLite database."""
    callbacks = importlib.import_module(PACKAGE + ".callbacks")
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    monkeypatch.setattr(callbacks, "_connect", lambda: conn)
    monkeypatch.setattr(callbacks, "_release", lambda c: None)
    return conn
