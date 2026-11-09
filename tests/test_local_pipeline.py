"""Practical test for the voice.start_local_pipeline pipeline module.

Exercises start_local_pipeline() directly so you can verify:
  - Without ELEVENLABS_API_KEY or OPENAI_API_KEY -> ok: False, clear error message (no crash)
  - With both keys set -> ok: True, pipeline starts and runs in background
    (pipecat-ai is installed in Hermes's Python venv)

Run:  python3 tests/test_local_pipeline.py
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pipeline import start_local_pipeline  # noqa: E402


async def main() -> None:
    print("=== Test 1: no API keys ===")
    os.environ.pop("ELEVENLABS_API_KEY", None)
    os.environ.pop("OPENAI_API_KEY", None)
    result = await start_local_pipeline(elevenlabs_api_key="", openai_api_key="")
    print("Result:", result)
    assert result["ok"] is False, "should fail without API keys"
    assert "ELEVENLABS_API_KEY" in result["error"], "should name the missing key"
    assert "OPENAI_API_KEY" in result["error"], "should name the missing key"
    print("PASS: graceful degradation without credentials")

    print("\n=== Test 2: only ElevenLabs key (missing OpenAI) ===")
    os.environ["ELEVENLABS_API_KEY"] = "el-test-key-12345"
    result = await start_local_pipeline(
        elevenlabs_api_key="el-test-key-12345", openai_api_key=""
    )
    print("Result:", result)
    assert result["ok"] is False, "should fail without OpenAI key"
    assert "OPENAI_API_KEY" in result["error"]
    print("PASS: clear error when OpenAI key missing")

    print("\n=== Test 3: both keys present (pipeline starts successfully) ===")
    os.environ["OPENAI_API_KEY"] = "sk-test-key-12345"
    result = await start_local_pipeline(
        elevenlabs_api_key="el-test-key-12345", openai_api_key="sk-test-key-12345"
    )
    print("Result:", result)
    assert result["ok"] is True, "pipeline should start with both keys"
    assert result["transport"] == "smallwebrtc"
    assert result["stt"] == "elevenlabs:scribe_v2"
    assert result["llm"] == "openai:gpt-5.6-luna"
    assert result["tts"] == "elevenlabs:eleven_flash_v2_5"
    assert "connection_id" in result
    assert result["signaling_url"] == "http://localhost:8765"
    print("PASS: pipeline started with correct models")

    print("\n=== Test 4: hook allows non-voice tools ===")
    from hooks import on_pre_tool_call  # noqa: E402
    verdict = on_pre_tool_call(tool_name="files.read")
    assert verdict is None, "non-voice tools should not be blocked"
    print("PASS: hook allows unrelated tools")

    print("\nAll tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
