# Checks

Before starting the next piece of work, confirm all of the following:

- [ ] The previous piece passed `hermes plugins validate` with "Validation passed."
- [ ] The previous piece passed `hermes plugins doctor` with the expected tool and hook counts
- [ ] A standalone smoke test loads `__init__.py` by file path and confirms `register(ctx)` wires the expected tools and hooks
- [ ] No phone or speech provider library is imported when the plugin starts — only inside tool handlers
- [ ] Every tool handler returns a clear "not configured" message instead of crashing when credentials are missing
- [ ] The pre-tool-call hook only blocks tools this plugin owns (names starting with `voice.`)
- [ ] Any manifest string containing a colon is quoted so YAML parses correctly

After writing code for a new piece, run these checks again before moving on:

- [ ] `hermes plugins validate <plugin-dir>` → "Validation passed."
- [ ] `hermes plugins doctor <plugin-dir>` → OK, registration counts match what was declared
- [ ] Smoke test asserts the new tool or hook name is present
- [ ] Read the real output from each command — do not guess whether it passed

## Tests

- [x] Confirm Pipecat is exactly 1.7.0 in the test runner, local runner, and Hermes plugin runtime; dependency declarations must match.
- [x] Locally verify greeting audio, a spoken turn and response, idle goodbye, and disconnect cleanup before modifying the adapter.
- [x] Adapter port must preserve Hermes as the LLM and must not treat Hermes response latency as caller silence (regression tests plus local audio with a simulated Hermes lifecycle).

- [x] `pytest tests` (run from the repo root; `tests/pytest.ini` keeps pytest from importing the root plugin `__init__.py`) -> 96 passed, 35 upstream deprecation warnings
- [ ] Deploy the new behaviour after reconciling the installed plugin's independent changes, then verify a real Hermes/Twilio call. No gateway restart or behaviour deployment performed in this pass.
