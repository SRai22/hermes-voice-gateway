# AGENTS.md

Hermes plugin (`kind: platform`) that answers Twilio phone calls, speaks Hermes' replies with ElevenLabs (Pipecat), and places operator-approved callbacks. See [README.md](README.md) for behaviour and setup, [docs/](docs/) for design notes.

## Layout
The repo root **is** the plugin package (flat modules, relative imports). Hermes loads it by file path.

- `__init__.py`: `register(ctx)` entry point. Must not import Hermes internals, Pipecat or Twilio at load time.
- `adapter.py`: Twilio call server (`/voice`, `/voice/outbound`, `/media`) and Hermes platform adapter.
- `callbacks.py`: callback proposals + `/voice-callback` operator approval; state in the plugin SQLite file.
- `tools.py` / `schemas.py`: agent tools (`voice_propose_callback`, `voice_check_call_status`, `voice_start_local_pipeline`) and their schemas.
- `hooks.py`: `pre_tool_call` guard. `_env.py`: read config/secrets here, not via raw `os.getenv`.
- `twilio_util.py` (pure: signatures, TwiML, allowlist, stream tokens), `twilio_rest.py` (stdlib REST client, blocking: use `asyncio.to_thread`).
- `voice_behavior.py`: greeting/profile/guardrails shared by adapter and local pipeline. Plain Python, no Pipecat import.
- `pipeline.py`, `frontend/`, `scripts/start_local_pipeline.py`: local WebRTC dev loop only.
- `refurbo_demo.py`, `voice/`: opt-in local demo and sample caller profile/inventory snapshot.

## Dev environment
- Python >= 3.11; install runtime and dev dependencies with `pip install -e '.[dev]'` from the repo root. Packaging uses Hatchling (`pyproject.toml`).
- Phone setup requires `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `ELEVENLABS_API_KEY`, and `VOICE_WEBHOOK_URL`; callbacks also need `TWILIO_PHONE_NUMBER`. The local pipeline needs `ELEVENLABS_API_KEY` and `OPENAI_API_KEY`, not Twilio.
- The phone server defaults to `127.0.0.1:8081`. Twilio needs a public HTTPS base URL with no trailing slash, an exact `/voice` webhook URL for signature validation, and proxy WebSocket upgrades on `/media`.

## Commands
- Tests: `pytest tests` from the repo root (needs the project deps installed, e.g. `pip install -e '.[dev]'`; `test_adapter.py` imports `fastapi`, which comes via Pipecat).
- Lint: `ruff check .` (dev extra; no repo-specific Ruff configuration).
- Local voice loop: `python scripts/start_local_pipeline.py`, then open http://127.0.0.1:8765/. This uses OpenAI directly, not Hermes' agent.
- `tests/pytest.ini` makes `tests/` the rootdir so the root `__init__.py` is never imported by pytest; `tests/_hermes_stubs.py` stubs Hermes' `gateway` package and loads the plugin the way Hermes does. Don't run bare `pytest` at the root.
- No checked-in CI workflow, lockfile, or dedicated build/type-check script; the build backend and explicit packaged-file list live in `pyproject.toml`.

## Conventions and invariants
- **Pipecat is pinned to exactly `1.7.0`**, in both `pyproject.toml` and `plugin.yaml` `python_dependencies` (and install hints). Change all together; `tests/test_pipecat_version.py` checks this.
- New runtime modules must be added to `[tool.hatch.build] only-include` in `pyproject.toml`; new env vars to `plugin.yaml` (`requires_env`/`optional_env`) and the README.
- Python >= 3.11; modules start with `from __future__ import annotations` and a docstring explaining purpose.
- Tool handlers: `handler(args: dict, **kwargs)` returning a JSON string; tool names use underscores.
- Keep the plugin loadable without Pipecat/Twilio SDK installed (lazy imports).
- Version lives in both `pyproject.toml` and `plugin.yaml`.

## Pitfalls
- Voice ingress is deliberately serialized through gateway task cleanup, not just `on_processing_complete`; preserve the guarded `_event_session_key` / `_session_tasks` / `_active_sessions` contract and `tests/test_voice_ingress.py` coverage. Do not re-enable gateway steer/merge for phone turns without per-event terminal receipts.
- `tests/test_pipecat_version.py` checks the installed Pipecat version too, not just dependency declarations. Shell Python and the Hermes-managed runtime may differ; missing Pipecat is not a valid full-suite environment.
- The local runner starts signaling and the pipeline in ONE process; running `frontend/signaling-server.py` alone cannot work. Signaling must import after pipeline startup to see the live loop. Restart the single-call runner between calls.
- `scripts/start_local_pipeline.py` reads `~/.hermes/.env`, not a repo-local or active-profile `.env`, and preserves already-exported environment values. `_env.secret` uses profile-scoped Hermes secrets when available, falling back to the environment outside Hermes.
- `voice/PROFILE.md` is a template, not automatically loaded. Set `VOICE_PROFILE_PATH` or copy it to `<HERMES_HOME>/voice/PROFILE.md`; profiles truncate at 6,000 characters. Set `VOICE_GREETING` separately.
- `VOICE_REFURBO_DEMO=true` enables local inventory/appointment tools only; it does not add them to the Twilio caller path. Demo appointments persist locally and do not reserve real slots or notify anyone; see `docs/refurbo-demo.md`.

## Security rules (do not weaken)
- Agents may only *propose* callbacks; there is deliberately no agent-callable approve tool, and spoken words never run `/commands`.
- Twilio webhooks are signature-checked; `VOICE_INSECURE_NO_SIGNATURE` is local-dev only. Callers must be on `VOICE_ALLOWED_USERS` (or explicit `VOICE_ALLOW_ALL_USERS=true`); the allowlist is a filter, not authentication.
- Never log or commit secrets (`TWILIO_*`, `ELEVENLABS_API_KEY`, `OPENAI_API_KEY`); `.env` is local only (use `.env.example`). Don't log caller transcripts above DEBUG.
- Refurbo inventory is a stale snapshot; prices are enquiry approximations, not quotes.
