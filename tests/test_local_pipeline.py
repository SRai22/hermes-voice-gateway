"""start_local_pipeline reports missing keys instead of crashing."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pipeline as pipeline_module  # noqa: E402
from pipeline import start_local_pipeline  # noqa: E402


@pytest.mark.parametrize(
    "set_var, missing_var",
    [(None, "ELEVENLABS_API_KEY"), (None, "OPENAI_API_KEY"), ("ELEVENLABS_API_KEY", "OPENAI_API_KEY")],
)
def test_missing_keys_named_in_error(monkeypatch, set_var, missing_var) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if set_var:
        monkeypatch.setenv(set_var, "test-key")
    result = asyncio.run(start_local_pipeline())
    assert result["ok"] is False
    assert missing_var in result["error"]
    if set_var:
        assert set_var not in result["error"]


def test_disconnect_cancels_pipeline_and_waits_for_cleanup(monkeypatch) -> None:
    async def scenario() -> None:
        cleanup_finished = asyncio.Event()

        async def running_pipeline() -> None:
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleanup_finished.set()

        run_task = asyncio.create_task(running_pipeline())
        monkeypatch.setattr(pipeline_module, "_RUN_TASK", run_task)
        monkeypatch.setattr(
            pipeline_module, "_RUN_INFO", {"connection_id": "test-connection"}
        )
        await asyncio.sleep(0)

        await pipeline_module._stop_pipeline_on_disconnect("test-connection")

        assert run_task.done()
        assert cleanup_finished.is_set()

    asyncio.run(scenario())


def test_disconnect_unblocks_an_end_frame_already_draining(monkeypatch) -> None:
    """A peer may leave before the goodbye finishes playing."""
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import CancelFrame, EndFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.workers.runner import WorkerRunner

    async def scenario() -> None:
        draining = asyncio.Event()

        class DisconnectedOutput(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, EndFrame):
                    draining.set()  # Playback cannot finish after the peer leaves.
                    return
                await self.push_frame(frame, direction)

        class Session:
            closed = False

            async def close(self):
                self.closed = True

        worker = PipelineWorker(Pipeline([DisconnectedOutput()]), enable_rtvi=False)
        runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
        session = Session()
        connection_id = "draining-connection"
        monkeypatch.setattr(pipeline_module, "ACTIVE_CONNECTIONS", {connection_id: object()})
        monkeypatch.setattr(pipeline_module, "_RUN_INFO", {"connection_id": connection_id})
        run_task = asyncio.create_task(
            pipeline_module._run_pipeline(runner, worker, session, connection_id)
        )
        monkeypatch.setattr(pipeline_module, "_RUN_TASK", run_task)
        await worker.queue_frame(EndFrame())
        await asyncio.wait_for(draining.wait(), 2)
        stopping = asyncio.create_task(pipeline_module._stop_pipeline_on_disconnect(connection_id))
        try:
            done, _ = await asyncio.wait({stopping}, timeout=1)
            assert done, "disconnect is stuck behind the draining EndFrame"
            await stopping
            assert run_task.done()
            assert session.closed
            assert connection_id not in pipeline_module.ACTIVE_CONNECTIONS
        finally:
            # Rescue the old implementation without leaving pytest hung on teardown.
            if not run_task.done():
                await worker.pipeline.queue_frame(CancelFrame())
            await asyncio.wait_for(asyncio.gather(run_task, stopping, return_exceptions=True), 3)

    asyncio.run(scenario())


def test_pipeline_has_greeting_profile_smart_turn_and_guardrails(monkeypatch, tmp_path) -> None:
    """Needs pipecat installed. Builds the real pipeline (no network) and fires its handlers."""
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import EndFrame, ErrorFrame, TTSSpeakFrame
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.transports.smallwebrtc import transport as webrtc
    from pipecat.workers.runner import WorkerRunner

    (tmp_path / "PROFILE.md").write_text("Speak like a pirate.", encoding="utf-8")
    monkeypatch.setenv("VOICE_PROFILE_PATH", str(tmp_path / "PROFILE.md"))
    monkeypatch.setenv("VOICE_GREETING", "Ahoy!")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    built, queued = {}, []

    async def fake_add(self, *workers):
        built["worker"] = workers[0]

    async def fake_run(self, *args, **kwargs):
        return None

    async def fake_queue(self, frame):
        queued.append(frame)

    class Spy(webrtc.SmallWebRTCTransport):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            built["transport"] = self

    monkeypatch.setattr(WorkerRunner, "add_workers", fake_add)
    monkeypatch.setattr(WorkerRunner, "run", fake_run)
    monkeypatch.setattr(PipelineWorker, "queue_frame", fake_queue)
    monkeypatch.setattr(webrtc, "SmallWebRTCTransport", Spy)
    monkeypatch.setattr(pipeline_module, "_RUN_TASK", None)

    async def scenario() -> None:
        assert (await start_local_pipeline())["ok"] is True
        await pipeline_module._RUN_TASK
        worker = built["worker"]

        def collect(p):
            yield p
            for child in getattr(p, "processors", []):
                yield from collect(child)

        procs = {type(p).__name__: p for p in collect(worker.pipeline)}
        names = [type(p).__name__ for p in collect(worker.pipeline)]
        assert names.index("OpenAILLMService") < names.index("ElevenLabsTTSService")
        assert names.index("SmallWebRTCOutputTransport") < names.index("LLMAssistantAggregator")

        # voice profile reaches the LLM; smart turn drives end-of-speech
        assert procs["OpenAILLMService"]._settings.system_instruction == "Speak like a pirate."
        stop = procs["LLMUserAggregator"]._user_turn_controller._user_turn_strategies.stop
        assert [type(s).__name__ for s in stop] == ["TurnAnalyzerUserTurnStopStrategy"]

        # A normal WebRTC peer close emits "closed", not "disconnected".
        disconnected = []

        async def record_disconnect(connection_id):
            disconnected.append(connection_id)

        monkeypatch.setattr(pipeline_module, "_stop_pipeline_on_disconnect", record_disconnect)
        connection = built["transport"]._client._webrtc_connection
        await connection._call_event_handler("closed")
        await asyncio.sleep(0.05)
        assert disconnected == [pipeline_module._RUN_INFO["connection_id"]]

        # greeting on connect
        await built["transport"]._call_event_handler("on_client_connected", None)
        await asyncio.sleep(0.05)  # Pipecat runs event handlers as tasks
        assert [f.text for f in queued if isinstance(f, TTSSpeakFrame)] == ["Ahoy!"]

        # guardrail: silence gets "Are you still there?"
        queued.clear()
        await procs["LLMUserAggregator"]._call_event_handler("on_user_turn_idle")
        await asyncio.sleep(0.05)
        assert [f.text for f in queued] == [pipeline_module.vb.IDLE_PROMPT]

        # guardrail: a turn with no words gets "say it again"; a turn with words does not
        queued.clear()
        agg = procs["LLMUserAggregator"]
        await agg._call_event_handler("on_user_turn_stopped", None, type("M", (), {"content": ""})())
        await agg._call_event_handler("on_user_turn_stopped", None, type("M", (), {"content": "hi"})())
        await asyncio.sleep(0.05)
        assert [f.text for f in queued] == [pipeline_module.vb.NOT_UNDERSTOOD]

        # guardrail: errors get an apology, the third ends the call
        queued.clear()
        for _ in range(3):
            await worker._call_event_handler("on_pipeline_error", ErrorFrame(error="boom"))
        await asyncio.sleep(0.05)
        spoken = [f.text for f in queued if isinstance(f, TTSSpeakFrame)]
        assert spoken[:2] == [pipeline_module.vb.ERROR_REPLY] * 2
        assert spoken[2] == pipeline_module.vb.ERROR_GOODBYE
        assert isinstance(queued[-1], EndFrame)

    asyncio.run(scenario())
