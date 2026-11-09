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
- Ran hermes plugins validate → passed
- Ran hermes plugins doctor → passed (4 tools, 1 hook registered)
- Wrote pipeline.py with full Pipecat 1.15.0 wiring (SmallWebRTCTransport, Deepgram STT/TTS, OpenAI LLM with echo fallback, UserTurnProcessor with VAD, RTVI, PipelineRunner)
- Added voice.start_local_pipeline async tool for local audio development before Twilio
- Added smallwebrtc to python_dependencies with upper bounds
- Registered voice.start_local_pipeline in __init__.py and declared it in plugin.yaml
- Updated smoke test to assert the new tool
- Installed pipecat-ai 1.15.0 and verified all API signatures (SmallWebRTCTransport, DeepgramSTTService, DeepgramTTSService, OpenAILLMService, UserTurnProcessor, Pipeline, PipelineTask, PipelineRunner, RTVIProcessor, RTVIObserver, LLMFullResponseAggregator)

## In progress
- Nothing yet

## Up next (one piece at a time)
- Verify Twilio call signatures before answering
- Save and show a proposed callback for human approval
- Look up a call's status from the phone provider

## Definition of done for each piece
- Hermes validate and doctor both pass
- A standalone smoke test confirms the new piece works
- No provider library (Pipecat, smallwebrtc, Twilio, Deepgram) is loaded when the plugin starts — only inside tool handlers
- Every tool handler returns a clear error instead of crashing when dependencies are missing
