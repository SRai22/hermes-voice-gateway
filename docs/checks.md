# Checks: Twilio inbound voice (platform adapter, stage 1)

Independent review. Format: requirement | verdict (pass/fail/unresolved) | evidence | correction

Suite state: hvg-venv python -m pytest tests -q -> 36 passed, 1 warning. pipecat-ai is NOT installed in that venv (only fastapi, starlette, uvicorn), so _run_call, TranscriptRelay and the real parse_telephony_websocket path never execute in tests.

| requirement | verdict | evidence | correction |
|---|---|---|---|
| Baseline: existing tests pass before changes | unresolved | claim 13 passed at 75c4bb0 not reproducible here; current tree runs 36 passed | none needed for current state |
| Plugin is kind: platform and registers voice via ctx.register_platform | pass (caveats F9, F10) | plugin.yaml:5 kind: platform; __init__.py:55-70 kwargs all appear in Hermes refs (sms_adapter.py:343-358, ADDING_A_PLATFORM.md); tests/test_plugin_scaffold.py | verify Platform("voice") (adapter.py:64) against real gateway.config.Platform; refs have no config.py |
| /voice rejects missing/invalid X-Twilio-Signature (403) | pass | adapter.py:196-200, twilio_util.py:46-56 fail closed on empty token/sig; test_missing_or_bad_signature_is_403 (3 params) pass | none |
| /voice rejects callers outside allowlist, opens no stream | pass (caveat F7) | adapter.py:205-207 returns Hangup TwiML before _tokens.issue; test_caller_not_on_allowlist_is_hung_up | From is spoofable caller ID; see F7 |
| Media WS refuses connection without valid one-time token | pass for no-pipeline-without-token; defect F1 | adapter.py:218-231; tokens popped on redeem (twilio_util.py:96-100), 60 s TTL; pipecat v1.7.0 runner/utils.py:230-236 puts customParameters in call_data body, so the token read is correct. The unit test passes only because pipecat is not importable (ImportError caught at adapter.py:224) | F1 pre-auth hang; add a real-pipecat test |
| Caller transcript reaches Hermes as MessageEvent (chat_id = caller number) | unresolved | static: STT emits one TranscriptionFrame per committed transcript (stt.py:934-972; timestamps path skipped since include_timestamps=False); adapter.py:322-334 builds event with chat_id=caller; tested only via _relay_to_hermes with fakes | needs a run with pipecat installed |
| Hermes reply (send) spoken into active call; no call -> success=False | pass (caveat F4) | adapter.py:144-153; PipelineTask.queue_frame exists (pipecat worker.py:717); TTSSpeakFrame passes relay (adapter.py:277) to TTS; tests test_send_* | F4 |
| Secrets via Hermes scoped secrets, never logged; phone numbers redacted | fail | adapter/hooks use _env.secret -> get_scoped_secret(name, default) (matches _shared.py:22). But pipeline.py:101-110 reads os.environ directly. Adapter logs use redact_phone and log no secrets. Pipecat/loguru logs transcripts at DEBUG (F6) | F5, F6 |
| Plugin loads with no Hermes, Pipecat or Twilio installed | pass | __init__.py, hooks.py, pipeline.py, tools.py, schemas.py have only stdlib imports at top level; adapter imported lazily (__init__.py:34-36); _env.py falls back on ImportError; test_plugin_scaffold passes | none (caveat: _env.secret catches only ImportError) |
| Real call against Twilio and a running Hermes | unresolved | not runnable here | owner manual test |

## Findings

| id | severity | location | scenario | evidence | correction |
|---|---|---|---|---|---|
| F1 | medium | adapter.py:218-222 | Unauthenticated client opens /media; server calls accept(), then parse_telephony_websocket awaits first and second message with no timeout. Client sends nothing (or one message) and holds the socket and a task forever; repeat to exhaust fds. | pipecat runner/utils.py:187-205 __anext__ without timeout; no asyncio.wait_for in adapter | wrap parse in asyncio.wait_for (about 10 s), close 1008 on timeout |
| F2 | medium | adapter.py:208, 228-233 | max_calls is checked only in /voice against active _calls; pending tokens are not counted and /media never checks the cap. N simultaneous signed webhooks (allow_all or many allowlisted callers) all get TwiML and all connect, exceeding VOICE_MAX_CALLS (ElevenLabs cost). Two near-simultaneous webhooks from one number both get tokens; the second stream is closed 1008 and that call drops. | code read; no await between the in-_calls check and the _calls assignment at /media, so no dict race, only the cap gap | count pending tokens + calls at /voice, re-check cap at /media |
| F3 | low-medium | adapter.py:112-114, 242-244, 232-235 | disconnect() while _run_call is still building (before call.task is set at :310) skips _end_call (task None); uvicorn should_exit then waits on the live WebSocket and the call keeps running. | _end_call guard on call.task None | track the websocket in _Call and close it in _end_call |
| F4 | low | adapter.py:144-153 | Caller hangs up; pipeline finished but _handle_media has not yet popped _calls (or after task.cancel()); Hermes reply goes to queue_frame (worker.py:717-732 just puts on a queue), returns success=True, nothing is spoken, no retry. | worker.py:648-657, 668-675 | check call.task.has_finished() before queueing, else return success=False |
| F5 | medium | pipeline.py:101-110 vs hooks.py:29 | Secrets held only in a Hermes profile scope: the hook (secret()) lets voice.start_local_pipeline through, then pipeline.py reads os.environ and reports missing keys; under multiplexing it can read another profile value. Violates the scoped-secret requirement. Its default STT model is also scribe_v2 vs the adapter scribe_v2_realtime. | grep os.environ in pipeline.py | use _env.secret in pipeline.py |
| F6 | medium | runtime (adapter.py:305-316) | Pipecat logs via loguru (default stderr sink, DEBUG): stt.py logs the committed transcript text (caller speech, PII) and runner/utils.py logs Parsed - Data with call_data including the stream token. The adapter does not quiet these, so redaction covers only its own logger. | pipecat v1.7.0 stt.py _on_committed_transcript, runner/utils.py (Parsed - Type) | set loguru level INFO+ or add a filter in the gateway process |
| F7 | design risk | adapter.py:205 | Allowlist authenticates by Twilio From, which PSTN callers can spoof; StirVerstat is not checked. A spoofed allowlisted number gets full Hermes agent access. Under allow_all, blocked-ID callers share one chat_id (one session, one slot). | Twilio webhook semantics; code read | optionally require verified STIR/SHAKEN or add a PIN |
| F8 | low | adapter.py:322-334 | Events keep default allow_gateway_control=True, so an exact spoken phrase matched by coerce_plaintext_gateway_command becomes /restart. Each transcript is its own create_task(handle_message), so ordering of two quick utterances is not guaranteed. | base.py:1553-1566; event.py allow_gateway_control | set it False for voice; serialize relays per call |
| F9 | low, unverified | plugin.yaml:9-23 | Secret markers use secret: true; the Hermes ref plugin.yaml uses password: true and prompt:. If the loader reads only password, the setup wizard shows tokens unmasked. Loader source not provided. | sms_plugin.yaml | confirm key name |
| F10 | unverified | adapter.py:64 | Platform("voice"): ref adapters use Platform.SMS; core Platform is an Enum and its handling of plugin values could not be checked (config.py absent from refs). The test stub replaces it with a lambda, hiding any ValueError. | tests/_hermes_stubs.py | verify against real Hermes |
| F11 | low | pyproject.toml:3 vs plugin.yaml:2 | version 0.1.0 vs 0.2.0 | read | align |
| F12 | info | adapter.py:251-252, 306, 316 | PipelineTask, PipelineRunner are deprecated aliases in v1.7.0 (worker.py:1318-1323, runner.py:23-34), and passing a worker to run() is deprecated (workers/runner.py:218-246); they work now, removed in 2.0, pin below 1.8 holds. All other API names and signatures used (handle_sigint, FastAPIWebsocketParams, on_client_disconnected, CommitStrategy.VAD, ElevenLabsRealtimeSTTSettings, ElevenLabsTTSSettings voice/model, TwilioFrameSerializer args, parse_telephony_websocket CallData body/stream_id) confirmed in v1.7.0. | pipecat source | migrate to PipelineWorker/WorkerRunner later |
| F13 | low | adapter.py:132-133 | Accepts http:// webhook URL (message says https), producing a ws:// stream URL that Twilio Media Streams reject; fails silently at call time. | code read | require https |
| F14 | low | adapter.py:187-192 | With chunked encoding (no Content-Length) the body is fully read by await request.body() before the size check, so an unsigned client can force an unbounded read before the 403. | code read | stream-limit the read |

Not verified: how Hermes routes status/progress messages through send() (they would be spoken); behaviour when ElevenLabs connect fails mid-call (no error handler ends the call); real Hermes Platform and plugin loader behaviour.

## Corrections applied after review

| finding | status | change |
|---|---|---|
| F1 media handshake hang | fixed | `asyncio.wait_for(..., HANDSHAKE_TIMEOUT)`; test `test_silent_media_connection_times_out` |
| F2 call cap | fixed | pending streams count toward the cap at `/voice`; cap re-checked at `/media`; same number twice is refused |
| F3 shutdown during setup | fixed | `_Call.websocket` is closed when no pipeline task exists yet |
| F4 reply after hang-up | fixed | `send()` returns `success=False` when `task.has_finished()` |
| F5 scoped secrets in dev pipeline | fixed | `pipeline.py` uses `_env.secret` |
| F6 transcripts in Pipecat logs | documented | README: run with `LOGURU_LEVEL=INFO`; not enforced in code |
| F7 spoofable caller ID | documented | README security notes; no code change |
| F8 spoken `/commands` | fixed | `allow_gateway_control=False` on voice events. Per-call ordering of two quick utterances is still not guaranteed |
| F9 `secret:` vs `password:` | changed to `password:` to match Hermes' SMS plugin; loader not verified |
| F10 `Platform("voice")` | verified | Hermes `Platform._missing_` creates and caches plugin values (gateway/config.py) |
| F11 version mismatch | fixed | both 0.2.0 |
| F12 deprecated `PipelineTask`/`PipelineRunner` aliases | open | deprecated since 1.3.0, still work on pinned 1.15.x (DeprecationWarning only); migrate to `PipelineWorker`/`WorkerRunner` before 2.0 |
| F13 `http://` URL | fixed | config check requires `https://` |
| F14 chunked body read | fixed | body is read in capped chunks |

Suite after fixes: 41 passed. Still unresolved: real call against Twilio and Hermes; pipecat-dependent paths (`_run_call`, `TranscriptRelay`) are not exercised in tests.

## Pipecat 1.15.0 upgrade

Pin moved from `>=1.7.0,<1.8.0` to `>=1.15.0,<1.16.0` (pyproject.toml, plugin.yaml, pipeline.py message). Installed 1.15.0 with the extras in the test venv. Every Pipecat name the plugin imports resolved, and the constructor arguments the adapter passes exist (checked by importing and inspecting signatures). New test `test_call_pipeline_builds_with_real_pipecat` builds the real call pipeline (transport, serializer, ElevenLabs STT and TTS, PipelineTask) with only `PipelineRunner.run` stubbed. Suite: 42 passed, 3 DeprecationWarnings (`PipelineTask`, `PipelineRunner`, and one raised inside Pipecat's own ElevenLabs TTS). Not run: the local dev pipeline's full start (needs live keys), a live call.

## Pipecat docs conformance pass (docs.pipecat.ai, 2026-11-09)

The Pipecat docs MCP (daily-docs.mcp.kapa.ai) answers 401 and needs an OAuth sign-in, so this pass used docs.pipecat.ai pages and the v1.15.0 source instead.

| reference | verdict | evidence / change |
|---|---|---|
| Use `PipelineWorker`/`WorkerRunner`, not deprecated `PipelineTask`/`PipelineRunner`; register with `add_workers` then `run()` | fixed | adapter.py and pipeline.py migrated; our DeprecationWarnings are gone |
| Telephony: pipeline rates 8000 in/out; `parse_telephony_websocket`; custom TwiML `<Parameter>` in `call_data["body"]` | pass | adapter.py `SAMPLE_RATE`, token read from body |
| `TwilioFrameSerializer`: creds for auto hang-up; `resampler_clear_after_secs=None` recommended on telephony | fixed | passed via `InputParams` |
| ElevenLabs STT/TTS configured through `settings=`, not deprecated `model`/`params`/`voice_id` | pass | adapter uses `ElevenLabsRealtimeSTTSettings`, `ElevenLabsTTSSettings` |
| Accept the websocket before building `FastAPIWebsocketTransport` | pass | `accept()` in `_handle_media` |
| Verify Twilio signatures on the public webhook | pass | `validate_signature`, tests |
| Max call duration is not enforced by Pipecat; use a timer (goodbye then `EndFrame`) | fixed | `VOICE_MAX_CALL_SECONDS`, `_enforce_max_duration`, test |
| Cleanup on disconnect | pass | `on_client_disconnected` cancels the worker; cleanup in `finally` after `run()` |
| Interruptions come from user-turn strategies in an LLM aggregator | open | no aggregator here, so no barge-in; documented in README |
| Carrier call-status events should drive end-of-call | open | not wired; README limit |
| Deprecation warning from `AudioContextTTSService` | not ours | raised inside Pipecat's ElevenLabs TTS |

Suite: 43 passed.

## Stage 2 review (callbacks, call status)

Independent reviewer found no path for an agent to dial or approve without a human, and no allowlist bypass (checked: no approve tool, `allow_gateway_control=False`, allowlist enforced at propose, approve and answer, atomic claim, once-only answer, signed-URL match). Findings and what was done:

| finding | status | change |
|---|---|---|
| 1 transport/parse errors escaped `_http`, leaving the row `dialing` | fixed | every OSError/HTTPException/bad-JSON becomes `TwilioError`; any other error in approve marks the row `failed`; tests |
| 2 `dialing` never ends if Twilio never calls back; `answered` never ends | fixed | `dialing` older than 3 min becomes `unanswered`; `answered` older than 2 h becomes `completed`; ring `Timeout=45`; test |
| 3 callback consumed when admission refused | fixed | row moves to `failed` when the callee could not be connected; test |
| 4 phone number in Twilio error text | fixed | `scrub_numbers` in `TwilioError`; log line no longer prints the message; test |
| 5 brief can spoof the approval line / carry instructions | fixed | listing prints the brief JSON-escaped; opening message frames the purpose as quoted data, not instructions; tests. Residual risk: how the model treats it is untested |
| 5b any Hermes-authorised user can approve | documented | Hermes gives plugin commands no sender identity; README says to restrict command access |
| 6 queue flooding; status lookup of any account call | fixed | max 2 pending per number; status only for calls we placed; tests |
| 6 insecure mode; sqlite lock errors | partly | insecure mode is dev-only and warned about; sqlite errors now return a friendly message |
| not verified | open | real `plugin_db` connection lifecycle and busy timeouts; whether other Hermes tools can invoke `/voice-callback`; live Twilio signature behaviour |

Suite after fixes: 71 passed.

## Call behaviour, local pipeline (self-review, no independent reviewer agent used)

| requirement | verdict | evidence | correction |
|---|---|---|---|
| Baseline before changes | pass | scratch venv pipecat 1.15.0: 71 passed | none |
| Greeting spoken on connect, configurable, can be off | pass | test_pipeline_has_greeting_... fires on_client_connected, sees TTSSpeakFrame("Ahoy!"); test_greeting_default_custom_and_off | audible result not heard |
| Voice profile reaches the LLM; default when missing/blank; capped | pass | same test checks OpenAILLMService system_instruction; test_profile_* | none |
| Smart turn drives end of speech | pass (wiring only) | aggregator stop strategies == [TurnAnalyzerUserTurnStopStrategy] | not run on live audio; stop_secs left at Pipecat's 3s fallback |
| Guardrails: idle, error, empty turn | pass for idle and error; unresolved for empty turn | idle/error handlers fired in test; handler fired in test (on_user_turn_stopped with empty content) | unverified that 1.7.0 fires on_user_turn_stopped for a no-transcript turn; needs a live noise test |
| Full suite | pass | 79 passed on pipecat 1.7.0 and 1.15.0 | none |
