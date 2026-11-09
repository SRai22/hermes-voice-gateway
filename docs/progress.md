# Progress

## Completed
- Scaffolded the Hermes native plugin for hermes-voice-gateway
- Wrote plugin.yaml manifest (name, version, tools, hooks, required env, config schema)
- Wrote __init__.py that registers four tools and one hook when Hermes loads the plugin
- Wrote schemas.py describing what each tool accepts as input
- Wrote tools.py with handlers that safely report "not configured" when phone/speech credentials are missing
- Wrote hooks.py that blocks voice tools until credentials are set up
- Wrote pyproject.toml for packaging
- Wrote a smoke test that confirms the plugin registers correctly
- Wrote a practical test for the local pipeline handler
- Ran hermes plugins validate → passed
- Ran hermes plugins doctor → passed (4 tools, 1 hook registered)
- Wrote pipeline.py with full Pipecat 1.7.0 wiring (SmallWebRTCTransport, ElevenLabs STT/TTS with Settings pattern, OpenAI LLM with OpenAILLMSettings, LLMContextAggregatorPair with SileroVADAnalyzer, RTVI, PipelineRunner) — models from env (ELEVENLABS_STT_MODEL=scribe_v2, ELEVENLABS_TTS_MODEL=eleven_flash_v2_5, OPENAI_MODEL=gpt-5.6-luna)
- Fixed critical bugs: global _ACTIVE_LOOP, signaling server imports pipeline module (not from pipeline import), frontend discovers connection id via GET /connections
- Added scripts/start_local_pipeline.py — combined pipeline + signaling runner in ONE process
- Added voice.start_local_pipeline async tool for local audio development before Twilio
- Switched STT/TTS from Deepgram to ElevenLabs, LLM to OpenAI (both required)
- Added smallwebrtc and openai to python_dependencies with upper bounds
- Registered voice.start_local_pipeline in __init__.py and declared it in plugin.yaml
- Updated smoke test and practical test for the new provider stack
- Verified API signatures against pipecat 1.15.0 (the pinned range): async add_ice_candidate, extras elevenlabs/openai/silero/webrtc, Python >=3.11
- Wrote frontend/index.html — browser WebRTC voice UI (connect, mute, volume, audio meters, transcript)
- Wrote frontend/signaling-server.py — HTTP signaling server bridging browser SDP offers to the running Pipecat pipeline
- Exposed ACTIVE_CONNECTIONS registry and _ACTIVE_LOOP in pipeline.py for signaling

- Audit fixes: signaling server bound to 127.0.0.1 with Origin check and same-origin UI; keys read from env only; stub tools return ok:False; single running pipeline with cleanup; ICE candidates parsed and awaited on the pipeline loop; deps trimmed and extras added; tests rewritten for pytest

- Twilio inbound calls (stage 1): plugin is now `kind: platform`. `adapter.py` answers calls (signature-checked webhook, allowlist, one-time stream token), runs a Pipecat pipeline (Twilio audio -> ElevenLabs realtime STT), hands transcripts to Hermes as messages, and speaks Hermes' replies with ElevenLabs TTS. `voice.route_inbound` removed (the adapter replaces it).
- Secrets read through Hermes scoped secrets (`_env.secret`), falling back to os.environ only without Hermes

- Stage 2: `voice_check_call_status` (Twilio REST lookup by call SID or callback id) and `voice_propose_callback` plus the human-only `/voice-callback list|show|approve|deny` command. Approval dials through Twilio; the callee's answer hits the signed `/voice/outbound` webhook, which opens the same media pipeline with an opening message for Hermes.
- Fixed from reading Hermes' dispatcher: tool handlers are now `handler(args: dict, **kwargs) -> JSON string`; tool names use underscores (`voice_*`) because model APIs reject dots.

## In progress
- Waiting on a real call test (needs Hermes + Twilio + a public URL); see docs/checks.md

## Up next (one piece at a time)
- Answering-machine detection for callbacks; Twilio status callbacks to update stored status
- Barge-in (caller interrupts Hermes mid-sentence) is not handled yet

## Definition of done for each piece
- Hermes validate and doctor both pass
- A standalone smoke test confirms the new piece works
- No provider library (Pipecat, smallwebrtc, ElevenLabs, OpenAI, Twilio) is loaded when the plugin starts — only inside tool handlers
- Every tool handler returns a clear error instead of crashing when dependencies are missing
