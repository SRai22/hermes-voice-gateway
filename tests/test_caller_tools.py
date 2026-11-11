"""Caller tools bind permissions and enquiry ownership to trusted live turns."""
from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hermes_stubs import PACKAGE, load_plugin, use_memory_db


@pytest.fixture()
def caller(monkeypatch):
    load_plugin()
    conn = use_memory_db(monkeypatch)
    module = importlib.import_module(PACKAGE + ".caller_tools")
    identity = module.CallerIdentity("session-one", "+15550001111", "CA-one", "CA-one-1")
    monkeypatch.setattr(module, "current_identity", lambda: identity)
    module.grant_turn(identity, ttl=300, is_active=lambda: True)
    yield module, identity, conn
    module.revoke_turn(identity)
    conn.close()


def test_search_uses_fixed_inventory_and_includes_snapshot_caveats(caller):
    module, _identity, _conn = caller
    result = json.loads(module.search_inventory({"model": "T14", "limit": 2}))
    assert result["ok"] and result["products"]
    assert len(result["products"]) <= 2
    assert all(product["url"].startswith("https://refurbo.in/") for product in result["products"])
    assert result["captured_at"]
    assert "approximate" in result["notice"].lower()
    assert "confirmation" in result["notice"].lower()


def test_enquiry_persists_trusted_ownership_and_retry_is_idempotent(caller):
    module, identity, conn = caller
    args = {"name": "Test Buyer", "requirements": "Office laptop, budget not decided", "consent": True}
    first = json.loads(module.save_enquiry(args))
    assert first["ok"] and first["status"] == "saved_for_operator_review"
    assert first["team_notified"] is False
    assert first["callback_approved"] is False
    assert "not" in first["notice"]
    second = json.loads(module.save_enquiry(args))
    assert second["enquiry_id"] == first["enquiry_id"]
    rows = conn.execute("SELECT id, call_sid, caller, payload FROM voice_enquiries").fetchall()
    assert len(rows) == 1
    assert rows[0][:3] == (first["enquiry_id"], identity.call_sid, identity.caller)
    assert json.loads(rows[0][3]) == args


@pytest.mark.parametrize("tool", ["terminal", "files.read", "delegate_task", "execute_code", "tool_call",
                                   "voice_propose_callback", "voice_check_call_status", "voice_start_local_pipeline"])
def test_voice_hook_denies_every_tool_outside_the_caller_allowlist(caller, monkeypatch, tool):
    module, _identity, _conn = caller
    hooks = importlib.import_module(PACKAGE + ".hooks")
    monkeypatch.setattr(module, "is_voice_context", lambda: True, raising=False)
    result = hooks.on_pre_tool_call(tool_name=tool)
    assert result is not None and result["action"] == "block"


def test_adapter_grants_only_the_live_turn_and_revokes_on_completion(caller, monkeypatch):
    module, _identity, conn = caller
    admod = importlib.import_module(PACKAGE + ".adapter")
    adapter = admod.VoiceAdapter(config=object())
    call = admod._Call(caller="+15550002222", call_sid="CA-live")
    adapter._calls[call.caller] = call
    hooks = importlib.import_module(PACKAGE + ".hooks")
    monkeypatch.setattr(module, "verify_tool_surface", lambda: None)
    monkeypatch.setattr(module, "is_voice_context", lambda: True)
    checked = []

    async def scenario():
        async def handle(event):
            identity = module.CallerIdentity(adapter._event_session_key(event), call.caller,
                                             call.call_sid, event.message_id)
            monkeypatch.setattr(module, "current_identity", lambda: identity)
            assert adapter.toolsets_for_source(event.source) == ["voice_caller", "no_mcp"]
            for name in module.TOOL_NAMES:
                assert hooks.on_pre_tool_call(tool_name=name) is None
            event._gateway_accepted = True
            await adapter.on_processing_complete(event, "success")
            for name in module.TOOL_NAMES:
                assert hooks.on_pre_tool_call(tool_name=name)["action"] == "block"
            checked.append(True)

        monkeypatch.setattr(adapter, "handle_message", handle)
        adapter._relay_to_hermes(call, "find an office laptop")
        await asyncio.wait_for(asyncio.gather(*adapter._background_tasks), 2)
        assert checked == [True]
        assert call.event_outcomes == {"CA-live-1": "success"}
        assert conn.execute("SELECT COUNT(*) FROM voice_caller_grants WHERE call_sid='CA-live'").fetchone() == (0,)

    asyncio.run(scenario())


@pytest.mark.parametrize("args", [
    {"inventory_path": "/etc/passwd"}, {"phone": "+15550002222"},
    {"limit": 6}, {"limit": True}, {"model": None}, {"budget_inr": -1}, {"ssd_gb": "512"},
])
def test_inventory_rejects_paths_identity_overrides_and_invalid_filters(caller, args):
    module, _identity, _conn = caller
    assert json.loads(module.search_inventory(args))["ok"] is False


@pytest.mark.parametrize("field,value", [
    ("consent", False), ("consent", "true"), ("name", " "), ("requirements", ""),
    ("requirements", "x" * 1001), ("model", ""), ("budget_inr", 0), ("ram_gb", True),
    ("ssd_gb", -1), ("phone", "+15550002222"), ("call_sid", "CA-other"),
    ("enquiry_id", "another-persons-record"), ("db_path", "/etc/passwd"),
])
def test_invalid_or_owner_overriding_enquiry_is_not_saved(caller, field, value):
    module, _identity, conn = caller
    args = {"name": "Buyer", "requirements": "Office laptop", "consent": True, field: value}
    assert json.loads(module.save_enquiry(args))["ok"] is False
    assert conn.execute("SELECT COUNT(*) FROM voice_enquiries").fetchone() == (0,)


def test_conflicting_retry_does_not_replace_enquiry(caller):
    module, _identity, conn = caller
    args = {"name": "Buyer", "requirements": "Office laptop", "consent": True}
    assert json.loads(module.save_enquiry(args))["ok"]
    assert json.loads(module.save_enquiry({**args, "requirements": "Different requirements"}))["ok"] is False
    assert json.loads(conn.execute("SELECT payload FROM voice_enquiries").fetchone()[0]) == args


@pytest.mark.parametrize("field,value", [
    ("session_key", "other-session"), ("caller", "+15550003333"),
    ("call_sid", "CA-other"), ("message_id", "CA-one-2"),
])
def test_cross_session_caller_call_or_turn_cannot_borrow_permission(caller, monkeypatch, field, value):
    module, identity, conn = caller
    monkeypatch.setattr(module, "current_identity", lambda: replace(identity, **{field: value}))
    assert json.loads(module.search_inventory({}))["ok"] is False
    assert json.loads(module.save_enquiry({"name": "Buyer", "requirements": "Laptop", "consent": True}))["ok"] is False
    assert conn.execute("SELECT COUNT(*) FROM voice_enquiries").fetchone() == (0,)


@pytest.mark.parametrize("state", ["revoked", "expired", "other_process", "hung_up"])
def test_inactive_permissions_deny_both_handlers_and_hook(caller, monkeypatch, state):
    module, identity, conn = caller
    hooks = importlib.import_module(PACKAGE + ".hooks")
    monkeypatch.setattr(module, "is_voice_context", lambda: True)
    if state == "revoked":
        module.revoke_turn(identity)
    elif state == "expired":
        conn.execute("UPDATE voice_caller_grants SET expires_at=0")
        conn.commit()
    elif state == "other_process":
        module._live_turns.clear()
    else:
        module._live_turns[identity] = lambda: False
    assert json.loads(module.search_inventory({}))["ok"] is False
    assert json.loads(module.save_enquiry({"name": "Buyer", "requirements": "Laptop", "consent": True}))["ok"] is False
    assert hooks.on_pre_tool_call(tool_name="voice_search_inventory")["action"] == "block"
    assert hooks.on_pre_tool_call(tool_name="voice_save_enquiry")["action"] == "block"


def test_revocation_storage_failure_still_closes_local_permission(caller, monkeypatch):
    module, identity, _conn = caller

    def fail_revoke(self, identity):
        raise sqlite3.OperationalError("storage unavailable")

    with monkeypatch.context() as patcher:
        patcher.setattr(module.CallerStore, "revoke", fail_revoke)
        with pytest.raises(sqlite3.OperationalError):
            module.revoke_turn(identity)
        assert json.loads(module.search_inventory({}))["ok"] is False


@pytest.mark.parametrize("missing", ["bound_key", "platform", "thread_id", "message_id"])
def test_real_identity_reader_rejects_env_only_or_incomplete_context(monkeypatch, missing):
    load_plugin()
    module = importlib.import_module(PACKAGE + ".caller_tools")
    context = ModuleType("gateway.session_context")
    values = {"HERMES_SESSION_PLATFORM": "voice", "HERMES_SESSION_CHAT_ID": "+15550001111",
              "HERMES_SESSION_THREAD_ID": "CA-one", "HERMES_SESSION_MESSAGE_ID": "CA-one-1"}
    if missing == "platform":
        values["HERMES_SESSION_PLATFORM"] = "telegram"
    elif missing != "bound_key":
        values["HERMES_SESSION_" + missing.upper()] = ""
    context.__dict__.update(
        get_session_env=lambda name, default="": values.get(name, default),
        bound_session_key=lambda: "" if missing == "bound_key" else "session-one",
    )
    monkeypatch.setitem(sys.modules, "gateway.session_context", context)
    assert json.loads(module.search_inventory({}, caller="+15550001111", call_sid="CA-one"))["ok"] is False
    assert json.loads(module.save_enquiry({"name": "Buyer", "requirements": "Laptop", "consent": True}))["ok"] is False


@pytest.mark.parametrize("extra", [None, "terminal", "delegate_task", "tool_call"])
def test_effective_tool_surface_must_equal_the_two_caller_tools(caller, monkeypatch, extra):
    module, _identity, _conn = caller
    context = ModuleType("gateway.session_context")
    context.__dict__.update(bound_session_key=lambda: "", get_session_env=lambda name: "")
    skill_utils = ModuleType("agent.skill_utils")
    skill_utils.__dict__["parse_config_string_list"] = lambda value: value or []
    config = ModuleType("hermes_cli.config")
    config.__dict__["load_config"] = lambda: {"platform_toolsets": {"voice": ["terminal"]}}
    tool_config = ModuleType("hermes_cli.tools_config")

    def resolve(config, platform):
        assert platform == "voice"
        assert config["platform_toolsets"]["voice"] == ["voice_caller", "no_mcp"]
        return {"voice_caller"}

    tool_config.__dict__["_get_platform_tools"] = resolve
    model_tools = ModuleType("model_tools")
    names = set(module.TOOL_NAMES) | ({extra} if extra else set())
    model_tools.__dict__["get_tool_definitions"] = lambda **kwargs: [{"function": {"name": name}} for name in names]
    for fake in (context, skill_utils, config, tool_config, model_tools):
        monkeypatch.setitem(sys.modules, fake.__name__, fake)
    if extra:
        with pytest.raises(PermissionError):
            module.verify_tool_surface()
    else:
        module.verify_tool_surface()


def test_concurrent_duplicate_enquiries_are_committed_once(caller, monkeypatch, tmp_path):
    module, identity, _conn = caller
    path = tmp_path / "caller.db"
    monkeypatch.setattr(module.callbacks, "_connect", lambda: sqlite3.connect(path))
    monkeypatch.setattr(module.callbacks, "_release", lambda conn: conn.close())
    module.grant_turn(identity, ttl=300, is_active=lambda: True)
    args = {"name": "Buyer", "requirements": "Office laptop", "consent": True}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: json.loads(module.save_enquiry(args)), range(2)))
    assert all(result["ok"] for result in results)
    assert results[0]["enquiry_id"] == results[1]["enquiry_id"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM voice_enquiries").fetchone() == (1,)
        assert json.loads(conn.execute("SELECT payload FROM voice_enquiries").fetchone()[0]) == args


def test_late_revoke_cannot_remove_the_next_turns_permission(caller):
    module, identity, _conn = caller
    module.revoke_turn(identity)
    next_turn = replace(identity, message_id="CA-one-2")
    module.grant_turn(next_turn, ttl=300, is_active=lambda: True)
    try:
        module.revoke_turn(identity)
        with module.open_caller_store() as store:
            store.authorize(next_turn)
    finally:
        module.revoke_turn(next_turn)


def test_a_live_lease_cannot_be_replaced_by_a_different_turn(caller):
    module, identity, _conn = caller
    with pytest.raises(PermissionError):
        module.grant_turn(replace(identity, message_id="CA-one-2"), ttl=300, is_active=lambda: True)
    assert module.require_access() == identity


@pytest.mark.parametrize("check", ["is_voice_context", "require_access"])
def test_unexpected_authorization_errors_return_a_block_not_a_fail_open_exception(caller, monkeypatch, check):
    module, _identity, _conn = caller
    hooks = importlib.import_module(PACKAGE + ".hooks")

    def broken(*args, **kwargs):
        raise RuntimeError("permission service failed")

    monkeypatch.setattr(module, check, broken)
    assert hooks.on_pre_tool_call(tool_name="voice_search_inventory")["action"] == "block"


def test_adapter_refuses_inference_when_tool_resolution_is_not_safe(caller, monkeypatch):
    module, _identity, conn = caller
    admod = importlib.import_module(PACKAGE + ".adapter")
    adapter = admod.VoiceAdapter(config=object())
    call = admod._Call(caller="+15550002222", call_sid="CA-unsafe")
    adapter._calls[call.caller] = call

    def unsafe():
        raise PermissionError("additional plugin tools were resolved")

    monkeypatch.setattr(module, "verify_tool_surface", unsafe)

    async def scenario():
        adapter._relay_to_hermes(call, "request")
        await asyncio.wait_for(asyncio.gather(*adapter._background_tasks), 2)
        assert adapter.handled == []
        assert call.event_outcomes == {"CA-unsafe-1": "failure"}
        assert not call.tool_grants
        assert conn.execute("SELECT COUNT(*) FROM voice_caller_grants WHERE call_sid='CA-unsafe'").fetchone() == (0,)

    asyncio.run(scenario())


@pytest.mark.parametrize("finish", ["hangup", "timeout", "rejected", "cancelled"])
def test_adapter_terminal_paths_revoke_permissions(caller, monkeypatch, finish):
    module, _identity, conn = caller
    admod = importlib.import_module(PACKAGE + ".adapter")
    adapter = admod.VoiceAdapter(config=object())
    call = admod._Call(caller="+15550002222", call_sid="CA-terminal")
    adapter._calls[call.caller] = call
    monkeypatch.setattr(module, "verify_tool_surface", lambda: None)
    if finish == "timeout":
        monkeypatch.setattr(admod, "_INGRESS_WAIT_SECONDS", 0.01)

    async def scenario():
        entered = asyncio.Event()

        async def handle(event):
            identity = module.CallerIdentity(adapter._event_session_key(event), call.caller,
                                             call.call_sid, event.message_id)
            monkeypatch.setattr(module, "current_identity", lambda: identity)
            assert module.require_access() == identity
            event._gateway_accepted = finish != "rejected"
            entered.set()
            if finish == "cancelled":
                raise asyncio.CancelledError
            if finish != "rejected":
                await asyncio.Event().wait()

        monkeypatch.setattr(adapter, "handle_message", handle)
        adapter._relay_to_hermes(call, "request")
        tasks = list(adapter._background_tasks)
        await asyncio.wait_for(entered.wait(), 2)
        if finish == "hangup":
            await adapter._close_call_ingress(call)
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        assert not call.tool_grants
        assert not call.pending_events
        assert json.loads(module.search_inventory({}))["ok"] is False
        assert conn.execute("SELECT COUNT(*) FROM voice_caller_grants WHERE call_sid='CA-terminal'").fetchone() == (0,)

    asyncio.run(scenario())


def test_caller_schemas_and_distribution_do_not_expose_ownership_or_paths():
    module = load_plugin()
    for schema in (module.CALLER_INVENTORY_SCHEMA, module.CALLER_ENQUIRY_SCHEMA):
        assert schema["additionalProperties"] is False
        assert not ({"caller", "phone", "destination", "call_sid", "session_key", "inventory_path", "db_path"}
                    & set(schema["properties"]))
    assert module.CALLER_ENQUIRY_SCHEMA["required"] == ["name", "requirements", "consent"]
    assert module.CALLER_ENQUIRY_SCHEMA["properties"]["consent"]["enum"] == [True]
    root = Path(__file__).resolve().parents[1]
    includes = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["hatch"]["build"]["only-include"]
    assert {"caller_tools.py", "callbacks.py", "twilio_rest.py", "voice/refurbo-inventory.json"} <= set(includes)
