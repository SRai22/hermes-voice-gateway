"""Serialized voice ingress owns every delivery until gateway task cleanup ends."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _hermes_stubs import load_adapter_module, use_memory_db


@pytest.fixture()
def ingress(monkeypatch):
    mod = load_adapter_module()
    use_memory_db(monkeypatch)
    monkeypatch.setattr(mod.caller_tools, "verify_tool_surface", lambda: None)
    adapter = mod.VoiceAdapter(config=object())
    call = mod._Call(caller="+15550001111", call_sid="CA-ingress")
    adapter._calls[call.caller] = call
    adapter._session_tasks = {}
    adapter._active_sessions = {}
    monkeypatch.setattr(adapter, "_event_session_key", lambda event: "canonical-session", raising=False)
    spoken = []

    async def say(call, text, hang_up=False):
        spoken.append(text)

    monkeypatch.setattr(adapter, "_say", say)
    return adapter, call, spoken


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def test_next_turn_waits_for_owner_cleanup_after_completion_hook(ingress, monkeypatch):
    adapter, call, spoken = ingress

    async def scenario():
        admitted = []
        cleanup = asyncio.Event()

        async def handle(event):
            assert "canonical-session" not in adapter._active_sessions
            admitted.append(event)
            event._gateway_accepted = True
            adapter._active_sessions["canonical-session"] = object()

            async def process():
                await adapter.on_processing_complete(event, "success")
                if len(admitted) == 1:
                    await cleanup.wait()
                adapter._active_sessions.pop("canonical-session")
                adapter._session_tasks.pop("canonical-session")

            adapter._session_tasks["canonical-session"] = asyncio.create_task(process())

        monkeypatch.setattr(adapter, "handle_message", handle)
        adapter._relay_to_hermes(call, "first")
        adapter._relay_to_hermes(call, "second")
        await until(lambda: len(admitted) >= 1 and "CA-ingress-1" not in call.pending_events)
        assert len(admitted) == 1, "completion hooks precede owner cleanup"
        cleanup.set()
        await asyncio.wait_for(asyncio.gather(*adapter._background_tasks), 2)
        assert [event.text for event in admitted] == ["first", "second"]
        assert not call.pending_events
        assert spoken == []

    asyncio.run(scenario())


def test_idle_updates_cannot_reorder_voice_turns(ingress, monkeypatch):
    adapter, call, spoken = ingress

    async def scenario():
        release = asyncio.Event()
        sync_calls = 0
        admitted = []

        async def sync(call):
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls == 1:
                await release.wait()

        async def handle(event):
            admitted.append(event.text)
            event._gateway_accepted = True
            await adapter.on_processing_complete(event, "success")

        monkeypatch.setattr(adapter, "_sync_idle", sync)
        monkeypatch.setattr(adapter, "handle_message", handle)
        adapter._relay_to_hermes(call, "first")
        adapter._relay_to_hermes(call, "second")
        await until(lambda: sync_calls >= 1)
        release.set()
        await asyncio.wait_for(asyncio.gather(*adapter._background_tasks), 2)
        assert admitted == ["first", "second"]
        assert spoken == []

    asyncio.run(scenario())


@pytest.mark.parametrize("anchored", [True, False])
def test_timed_out_turn_cannot_speak_a_late_reply(ingress, monkeypatch, anchored):
    adapter, call, spoken = ingress

    async def scenario():
        release = asyncio.Event()
        admitted = []

        async def handle(event):
            admitted.append(event)
            event._gateway_accepted = True
            adapter._active_sessions["canonical-session"] = object()

            async def process():
                try:
                    await release.wait()
                finally:
                    adapter._active_sessions.pop("canonical-session")
                    adapter._session_tasks.pop("canonical-session")

            adapter._session_tasks["canonical-session"] = asyncio.create_task(process())

        monkeypatch.setattr(sys.modules[adapter.__class__.__module__], "_INGRESS_WAIT_SECONDS", 0.01)
        monkeypatch.setattr(adapter, "handle_message", handle)
        adapter._relay_to_hermes(call, "question")
        await asyncio.wait_for(asyncio.gather(*adapter._background_tasks), 2)
        owner = adapter._session_tasks["canonical-session"]
        assert not owner.done(), "delivery timeout must not cancel running tools"
        assert call.event_outcomes == {"CA-ingress-1": "failure"}
        call.task = SimpleNamespace(has_finished=lambda: False)
        result = await adapter.send(call.caller, "late answer", reply_to=admitted[0].message_id if anchored else None,
                                    metadata={"notify": True, "thread_id": call.call_sid})
        release.set()
        await owner
        assert not result.success
        assert spoken == [sys.modules[adapter.__class__.__module__].vb.ERROR_REPLY]

    asyncio.run(scenario())


@pytest.mark.parametrize("contract", ["missing_tasks", "bad_owner", "orphan_guard", "bad_key"])
def test_unsupported_gateway_contract_fails_closed(ingress, monkeypatch, contract):
    adapter, call, spoken = ingress
    if contract == "missing_tasks":
        del adapter._session_tasks
    elif contract == "bad_owner":
        adapter._session_tasks["canonical-session"] = object()
    elif contract == "orphan_guard":
        adapter._active_sessions["canonical-session"] = object()
    else:
        monkeypatch.setattr(adapter, "_event_session_key", lambda event: None)

    async def scenario():
        adapter._relay_to_hermes(call, "question")
        await asyncio.wait_for(asyncio.gather(*adapter._background_tasks), 2)
        assert not adapter.handled
        assert not call.pending_events
        assert call.event_outcomes == {"CA-ingress-1": "failure"}
        assert len(spoken) == 1
        assert not adapter._ingress_lanes

    asyncio.run(scenario())


def test_missing_completion_hook_settles_as_failure(ingress, monkeypatch):
    adapter, call, spoken = ingress

    async def scenario():
        async def handle(event):
            event._gateway_accepted = True
            adapter._active_sessions["canonical-session"] = object()

            async def process():
                adapter._active_sessions.pop("canonical-session")
                adapter._session_tasks.pop("canonical-session")

            adapter._session_tasks["canonical-session"] = asyncio.create_task(process())

        monkeypatch.setattr(adapter, "handle_message", handle)
        adapter._relay_to_hermes(call, "question")
        await asyncio.wait_for(asyncio.gather(*adapter._background_tasks), 2)
        assert not call.pending_events
        assert call.event_outcomes == {"CA-ingress-1": "failure"}
        assert len(spoken) == 1

    asyncio.run(scenario())


def test_completion_before_admission_return_is_not_reported_as_rejected(ingress, monkeypatch):
    adapter, call, spoken = ingress

    async def scenario():
        async def handle(event):
            event._gateway_accepted = False
            await adapter.on_processing_complete(event, "success")
            await adapter.on_processing_complete(event, "failure")

        monkeypatch.setattr(adapter, "handle_message", handle)
        adapter._relay_to_hermes(call, "question")
        await asyncio.wait_for(asyncio.gather(*adapter._background_tasks), 2)
        assert call.event_outcomes == {"CA-ingress-1": "success"}
        assert not call.pending_events
        assert spoken == []

    asyncio.run(scenario())


@pytest.mark.parametrize("started", [True, False])
def test_hangup_settles_active_and_queued_delivery_without_cancelling_tools(ingress, started):
    adapter, call, spoken = ingress

    async def scenario():
        owner = None
        adapter._relay_to_hermes(call, "first")
        adapter._relay_to_hermes(call, "second")
        if started:
            await adapter.wait_for_messages()
            owner = next(iter(adapter._session_tasks.values()))
        await adapter._close_call_ingress(call)
        assert not call.pending_events
        assert call.event_outcomes == {"CA-ingress-1": "cancelled", "CA-ingress-2": "cancelled"}
        assert len(adapter.handled) == (1 if started else 0)
        assert not call.relay_tasks
        assert not adapter._ingress_lanes
        adapter._relay_to_hermes(call, "after hangup")
        assert call.turns == 2
        if owner is not None:
            assert not owner.done()
            owner.cancel()
            await asyncio.gather(owner, return_exceptions=True)
        assert spoken == []

    asyncio.run(scenario())


def test_gateway_shutdown_closes_ingress_before_base_cancellation(ingress):
    adapter, call, spoken = ingress

    async def scenario():
        adapter._relay_to_hermes(call, "first")
        adapter._relay_to_hermes(call, "second")
        await adapter.wait_for_messages()
        await adapter.cancel_background_tasks()
        assert call.ending and adapter._ingress_stopping
        assert not call.pending_events
        assert set(call.event_outcomes.values()) == {"cancelled"}
        assert not adapter._session_tasks
        assert not adapter._ingress_lanes
        assert spoken == []

    asyncio.run(scenario())


def test_hangup_does_not_queue_idle_frames_on_a_dead_pipeline(ingress, monkeypatch):
    adapter, call, spoken = ingress

    async def scenario():
        async def dead_pipeline(call):
            await asyncio.Event().wait()

        monkeypatch.setattr(adapter, "_sync_idle", dead_pipeline)
        call.pending_events.add("CA-ingress-1")
        await asyncio.wait_for(adapter._close_call_ingress(call), 0.1)
        assert call.event_outcomes == {"CA-ingress-1": "cancelled"}
        assert not call.pending_events
        assert spoken == []

    asyncio.run(scenario())


def test_existing_session_owner_and_its_drain_finish_before_voice_admission(ingress, monkeypatch):
    adapter, call, spoken = ingress

    async def scenario():
        release_owner, release_drain = asyncio.Event(), asyncio.Event()
        draining = asyncio.Event()
        admitted = []
        adapter._active_sessions["canonical-session"] = object()

        async def drain():
            draining.set()
            await release_drain.wait()
            adapter._session_tasks.pop("canonical-session")
            adapter._active_sessions.pop("canonical-session")

        async def owner():
            await release_owner.wait()
            adapter._session_tasks["canonical-session"] = asyncio.create_task(drain())

        adapter._session_tasks["canonical-session"] = asyncio.create_task(owner())

        async def handle(event):
            assert "canonical-session" not in adapter._active_sessions
            admitted.append(event.text)
            event._gateway_accepted = True
            await adapter.on_processing_complete(event, "success")

        monkeypatch.setattr(adapter, "handle_message", handle)
        adapter._relay_to_hermes(call, "question")
        release_owner.set()
        await asyncio.wait_for(draining.wait(), 2)
        assert admitted == []
        release_drain.set()
        await asyncio.wait_for(asyncio.gather(*adapter._background_tasks), 2)
        assert admitted == ["question"]
        assert not call.pending_events
        assert not adapter._ingress_lanes
        assert spoken == []

    asyncio.run(scenario())
