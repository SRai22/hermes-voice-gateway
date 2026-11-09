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

- [ ] `pytest tests` (run from the repo root; `tests/pytest.ini` keeps pytest from importing the root plugin `__init__.py`) -> all pass
