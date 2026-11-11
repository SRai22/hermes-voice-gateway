# hermes-voice-gateway
Hermes Voice Gateway connects your business phone line to Hermes. It answers calls, offers a restricted inventory/enquiry workflow, and makes operator-approved callbacks.

## Status
Early-stage (v0.2). Inbound calls, operator-approved callbacks and call-status lookup are implemented, with a local browser pipeline for development. Not production-hardened: it has not been load-tested or verified at scale, and the limits below apply.

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

## Caller inventory and enquiries
Phone turns can use only `voice_search_inventory` and `voice_save_enquiry`. The adapter overrides the caller toolsets with `voice_caller` and `no_mcp`; shell, file, browser, delegation, execution wrappers, connectors, callback/status tools and local-pipeline startup are not caller permissions. Operator-side callback tools and `/voice-callback` approval remain separate.

- Inventory search reads a packaged sample inventory snapshot (see [Demo data](#demo-data)), returns source URLs and caveats, and caps each response at five products. Model/title, maximum INR budget and minimum RAM/SSD filters are optional. There is no caller-controlled path or live-stock API.
- Saving requires a confirmed name, requirements and explicit `consent=true` to storing the enquiry with the current caller number. Unknown model, budget, RAM or SSD fields should be omitted. The caller cannot supply a different phone number, Call SID, database path or enquiry owner.
- Enquiries are plaintext SQLite records in `<HERMES_HOME>/plugin-data/hermes-voice-gateway/data.db`, table `voice_enquiries`. One enquiry is saved per call: identical retries return the same ID; changed details are refused rather than silently replacing the record. The response confirms local storage only: no team is notified, no slot is reserved and no callback is approved or scheduled. Operator viewing/handling is a separate follow-up feature.
- Both handlers require matching task-bound gateway session/caller/thread/message identity, a bounded database grant and a live call in the admitting gateway process. Permissions close on completion, rejection, timeout, cancellation or hangup. Environment-only identities, stale calls and worker/host processes without the live grant are denied. Treat caller ID as unverified, not proof of a person's identity. Do not collect payment credentials or other sensitive data in enquiries.

**Runtime policy prerequisite:** Hermes can automatically append other installed plugin toolsets, context-engine tools or tool-search wrappers even after a platform override. Before each caller turn, the plugin checks the resolved tool definitions and refuses inference unless they are exactly the two caller tools. The pre-tool hook also explicitly denies all other tool names and denies permission-check errors; handler checks remain mandatory even for direct dispatch.

In the active Hermes profile, save a Voice tool selection containing only `voice_caller`, with MCP disabled. `known_plugin_toolsets.voice` must include the omitted installed plugin toolsets (including `voice`) so Hermes does not add them by default. For a profile with only this plugin, that list is `[voice, voice_caller]`. Additional plugin toolsets must also be marked known-and-omitted for Voice. Context-engine additions and tool-search deferral must not broaden/replace the two exposed tool names. Use Hermes' tools/configuration interfaces; this plugin does not rewrite profile settings. Misconfiguration produces a caller-policy error in gateway logs and fails closed. This policy/context integration is checked against the installed Hermes APIs and must be reverified on runtime upgrades.

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
- **Caller-profile template**: [`voice/PROFILE.md`](voice/PROFILE.md) is a sample laptop-sales profile (requirements intake, shortlist, privacy rules). Copy it to the profile location above or set `VOICE_PROFILE_PATH` to its absolute path; the repo file is not loaded automatically. The caller tool allowlist is enforced independently of the profile; a prompt is not an access-control boundary. Profiles are truncated at 6,000 characters. Set `VOICE_GREETING` separately to an opening that discloses the caller is talking to an AI, because the gateway's fixed greeting is not generated from the profile.
- **Smart turn**: Pipecat's local smart-turn model decides from the audio when the caller has finished, instead of replying after any pause.
- **Guardrails**: a turn with no recognised words makes the agent ask the caller to repeat (up to twice in a row); silence gets "Are you still there?" and then a hang-up; three pipeline errors in one call end it with an apology.
- **Adapter timing**: one completed smart turn becomes one Hermes message. Idle detection pauses while Hermes is processing and while speech is queued or playing. Outbound callbacks retain their approved-purpose, Hermes-first opening instead of speaking a second generic greeting.
- **Reply routing**: each phone call has its own Hermes thread, keyed by Call SID. Speech delivery requires that call's thread or a valid reply-to turn; stale or unscoped replies cannot reach a later call from the same number. Only final user-facing text marked by Hermes with `notify=true` is spoken; busy acknowledgments and delivery-recovery notices stay silent. Direct adapter integrations must preserve that routing metadata.
- **Serialized phone turns**: voice inputs run FIFO per canonical Hermes session, waiting until the gateway processing task exits, including cleanup. Phone input does not steer or merge into an in-progress response; audio playback interruption is still separate. Delivery bookkeeping settles once on completion, rejection, failure or hangup. A five-minute admission/processing wait fails the delivery without cancelling running tools, and late replies to that delivery are refused. Hangup drops queued delivery, not tool side effects already in progress. This relies on guarded private Hermes session/task APIs; incompatible state fails closed instead of guessing that input was accepted.

## Local dev (no phone)
Use **Pipecat 1.7.0 exactly**, not a floating 1.7.x or 1.15.x release. Both dependency declarations and install hints use the same pin. Verify the actual interpreter as well as the dependency files; the Hermes-managed runtime and a shell's Python can be different environments.

`voice_start_local_pipeline` and `frontend/` give a browser-mic test loop that does not involve Twilio or Hermes' agent. Run `python scripts/start_local_pipeline.py`, then open http://127.0.0.1:8765/. Dev only; it is not part of the shipped plugin.

## Tests
`pytest tests` from the repo root.

Tests simulate the model and audio; they are not a live phone or provider test. Caller-permission coverage is in `tests/test_caller_tools.py`.

## Demo data
The opt-in local demo (`VOICE_REFURBO_DEMO=true`) uses a sample laptop-store inventory snapshot ([`voice/refurbo-inventory.json`](voice/refurbo-inventory.json)) and simulated appointments stored locally. The snapshot is demo data only: not live stock, and its prices are approximate and not quotes. The demo does not notify anyone, reserve real calendar slots or place calls, and does not change human approval for real callbacks. See [`docs/refurbo-demo.md`](docs/refurbo-demo.md).

## License
MIT. See [LICENSE](LICENSE).
