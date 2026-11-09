"""start_local_pipeline reports missing keys instead of crashing."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
