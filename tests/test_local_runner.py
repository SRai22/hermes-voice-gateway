"""The standalone runner must not load secrets from any dotenv file."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType


def test_runner_does_not_load_home_dotenv(monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes/.env").write_text("VOICE_RUNNER_TEST_SECRET=owner-only\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("VOICE_RUNNER_TEST_SECRET", raising=False)
    pipeline = ModuleType("pipeline")
    pipeline.__dict__["start_local_pipeline"] = lambda: None
    monkeypatch.setitem(sys.modules, "pipeline", pipeline)
    root = Path(__file__).resolve().parents[1]
    runpy.run_path(str(root / "scripts/start_local_pipeline.py"))
    import os

    assert "VOICE_RUNNER_TEST_SECRET" not in os.environ
