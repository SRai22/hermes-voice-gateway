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
