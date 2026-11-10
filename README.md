# hermes-voice-gateway
Hermes Voice Gateway connects your business phone line to Hermes. It answers calls, uses the tools your team already works with, and makes approved callbacks.

## Status
Inbound calls, operator-approved callbacks and call-status lookup are built. The local WebRTC pipeline has passed real ElevenLabs/OpenAI audio checks. The adapter has passed real ElevenLabs audio over a local Twilio-format WebSocket with a simulated Hermes lifecycle, plus regression tests. A real Twilio call through the running Hermes gateway is still unverified.

## How a call works
Twilio -> `POST <VOICE_WEBHOOK_URL>/voice` (signature checked) -> TwiML opens a media stream to `/media` -> ElevenLabs speech-to-text -> the transcript goes to Hermes as a normal message from the caller's number -> Hermes' reply is spoken with ElevenLabs.

## Setup
1. Install the plugin in Hermes and add these to Hermes' `.env`:
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `ELEVENLABS_API_KEY`
   - `VOICE_WEBHOOK_URL`: the public https base URL Twilio can reach, no trailing slash (a tunnel such as ngrok works for local testing)
   - `VOICE_ALLOWED_USERS`: comma-separated caller numbers (E.164). With no list, every call is refused, unless `VOICE_ALLOW_ALL_USERS=true`.
2. In the Twilio console, set the number's "A call comes in" webhook to `<VOICE_WEBHOOK_URL>/voice` (HTTP POST). The URL must match exactly, because it is part of the signature check.
3. The call server listens on `127.0.0.1:8081` by default (`VOICE_WEBHOOK_HOST`, `VOICE_WEBHOOK_PORT`). Point your tunnel or reverse proxy at it. It must forward WebSocket upgrades on `/media`.

Optional: `VOICE_MAX_CALLS` (default 5), `VOICE_MAX_CALL_SECONDS` (default 1800; 0 = no limit), `ELEVENLABS_VOICE_ID`, `ELEVENLABS_STT_MODEL`, `ELEVENLABS_TTS_MODEL`. `VOICE_INSECURE_NO_SIGNATURE=true` turns off signature checks (local dev only).

## Callbacks (outbound calls)
1. The agent calls the `voice_propose_callback` tool with a number, a one-sentence purpose and who asked. The number must be on `VOICE_ALLOWED_USERS`. This only records the request; nothing is dialed.
2. A human reviews it in Hermes: `/voice-callback list`, then `/voice-callback approve <id>` or `/voice-callback deny <id>`. Proposals expire after 24 hours, and at most 20 can wait at once.
3. On approval the gateway asks Twilio to call the number from `TWILIO_PHONE_NUMBER`. When they answer, Twilio fetches `<VOICE_WEBHOOK_URL>/voice/outbound?cb=<id>` (signature checked, usable once, only for the number that was dialed) and the call runs like an inbound call, except the agent speaks first and is told the approved purpose.
4. The agent can check progress with `voice_check_call_status`, by `callback_id` or by the `call_sid` of a call this gateway placed. It cannot look up other calls on your Twilio account.

Callback statuses: `pending`, `denied`, `expired` (24 h), `dialing`, `unanswered` (Twilio never reached the answer URL within 3 minutes: no answer, busy or rejected), `answered`, `completed` (2 h after answering), `failed`. At most 20 proposals can wait, and at most 2 per number.

There is deliberately no agent-callable approve tool, and spoken words cannot run `/commands`. The "requested by" name is whatever the agent supplied and is not verified. Hermes does not tell a plugin command who ran it, so anyone Hermes lets run slash commands can approve: restrict command access in Hermes to your operators. State is kept in the plugin's own SQLite file (`<HERMES_HOME>/plugin-data/hermes-voice-gateway/data.db`).

Needs `TWILIO_PHONE_NUMBER` in addition to the inbound settings.

## Speech recognition
Phone calls use `scribe_v2` through `ElevenLabsSTTService`, matching the local development pipeline. Silero VAD detects the end of each spoken segment, then the audio is uploaded for transcription. Pause after speaking to let Hermes respond; this is segmented HTTP transcription, not the realtime WebSocket API. Each call owns an HTTP session that closes when the call ends or setup fails.

## Limits (from the Pipecat telephony docs)
- Twilio WebSocket calls have basic call control only: no transfers or reconnects. Those need Daily PSTN/SIP.
- Pipecat's user-turn strategies can interrupt queued speech. Cancellation of Hermes' ongoing reasoning/tools is not coordinated with playback interruption, so full phone-call barge-in remains unverified.
- A dropped connection ends the call. Twilio call-status callbacks are not wired in, so a callback's stored status stays `answered` after the call ends (use `voice_check_call_status` for the live state).
- Calls to voicemail are not detected; the agent may speak to an answering machine.

## Security notes
- The allowlist trusts the caller ID Twilio reports, and phone caller IDs can be spoofed. Treat it as a filter, not as strong authentication. Do not give allowlisted callers more access than you would give anyone who can phone in.
- Pipecat logs caller speech (the transcript) at DEBUG level. Run Hermes with `LOGURU_LEVEL=INFO` or higher so transcripts stay out of logs.
- Spoken words never run Hermes `/commands`; calls are plain conversation only.

## Call behaviour (local pipeline and Twilio adapter)
- **Greeting**: the agent speaks first when a call connects. `VOICE_GREETING` overrides the text; `off` disables it.
- **Voice profile**: `<HERMES_HOME>/voice/PROFILE.md` (or `VOICE_PROFILE_PATH`) supplies short-reply, speakable instructions. If missing or empty, a built-in profile is used. Locally it is the OpenAI system instruction; the adapter snapshots it per call and supplies it to Hermes through `channel_prompt`, without changing the caller's transcript or replacing Hermes' own safety instructions.
- **Smart turn**: Pipecat's local smart-turn model decides from the audio when the caller has finished, instead of replying after any pause.
- **Guardrails**: a turn with no recognised words makes the agent ask the caller to repeat (up to twice in a row); silence gets "Are you still there?" and then a hang-up; three pipeline errors in one call end it with an apology.
- **Adapter timing**: one completed smart turn becomes one Hermes message. Idle detection pauses while Hermes is processing and while speech is queued or playing. Outbound callbacks retain their approved-purpose, Hermes-first opening instead of speaking a second generic greeting.

## Local dev (no phone)
Use **Pipecat 1.7.0 exactly**, not a floating 1.7.x or 1.15.x release. Both dependency declarations and install hints use the same pin. Verify the actual interpreter as well as the dependency files; the Hermes-managed runtime and a shell's Python can be different environments.

`voice_start_local_pipeline` and `frontend/` give a browser-mic test loop that does not involve Twilio or Hermes' agent. Run `python scripts/start_local_pipeline.py`, then open http://127.0.0.1:8765/. Dev only; it is not part of the shipped plugin.

## Tests
`pytest tests` from the repo root.
