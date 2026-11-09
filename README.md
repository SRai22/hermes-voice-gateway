# hermes-voice-gateway
Hermes Voice Gateway connects your business phone line to Hermes. It answers calls, uses the tools your team already works with, and makes approved callbacks.

## Status
Inbound calls work in code and tests but have **not** been tried against a live Twilio number. Callbacks and call-status lookup are not implemented yet.

## How a call works
Twilio -> `POST <VOICE_WEBHOOK_URL>/voice` (signature checked) -> TwiML opens a media stream to `/media` -> ElevenLabs speech-to-text -> the transcript goes to Hermes as a normal message from the caller's number -> Hermes' reply is spoken with ElevenLabs.

## Setup
1. Install the plugin in Hermes and add these to Hermes' `.env`:
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `ELEVENLABS_API_KEY`
   - `VOICE_WEBHOOK_URL`: the public https base URL Twilio can reach, no trailing slash (a tunnel such as ngrok works for local testing)
   - `VOICE_ALLOWED_USERS`: comma-separated caller numbers (E.164). With no list, every call is refused, unless `VOICE_ALLOW_ALL_USERS=true`.
2. In the Twilio console, set the number's "A call comes in" webhook to `<VOICE_WEBHOOK_URL>/voice` (HTTP POST). The URL must match exactly, because it is part of the signature check.
3. The call server listens on `127.0.0.1:8081` by default (`VOICE_WEBHOOK_HOST`, `VOICE_WEBHOOK_PORT`). Point your tunnel or reverse proxy at it. It must forward WebSocket upgrades on `/media`.

Optional: `VOICE_MAX_CALLS` (default 5), `ELEVENLABS_VOICE_ID`, `ELEVENLABS_STT_MODEL`, `ELEVENLABS_TTS_MODEL`. `VOICE_INSECURE_NO_SIGNATURE=true` turns off signature checks (local dev only).

## Security notes
- The allowlist trusts the caller ID Twilio reports, and phone caller IDs can be spoofed. Treat it as a filter, not as strong authentication. Do not give allowlisted callers more access than you would give anyone who can phone in.
- Pipecat logs caller speech (the transcript) at DEBUG level. Run Hermes with `LOGURU_LEVEL=INFO` or higher so transcripts stay out of logs.
- Spoken words never run Hermes `/commands`; calls are plain conversation only.

## Local dev (no phone)
`voice.start_local_pipeline` and `frontend/` give a browser-mic test loop that does not involve Twilio or Hermes' agent. Run `python scripts/start_local_pipeline.py`, then open http://127.0.0.1:8765/. Dev only; it is not part of the shipped plugin.

## Tests
`pytest tests` from the repo root.
