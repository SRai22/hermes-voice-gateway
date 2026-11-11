# Refurbo first-contact sales demo

## What the assistant does

This is a simulated first-contact assistant for Refurbo, the refurbished laptop marketplace at https://refurbo.in/laptop. It asks what the buyer needs, suggests suitable laptops from a saved website snapshot, and records a preferred sales-call appointment in a local demo CRM. It is not a deployed Refurbo service or an integration with the company's actual CRM.

The conversation should collect, one question at a time:

- What the laptop is for: study, office work, programming, design, gaming, or something else.
- Budget in Indian rupees and any model or brand preference.
- Required RAM and SSD capacity. If the buyer is unsure, explain options simply and confirm the recommendation rather than inventing a requirement.
- A name and callback number, with consent to a sales follow-up.
- A specific future date, time, and time zone. Default to asking whether India Standard Time is correct, not assuming the caller's location.

Summarize the laptop requirements and read back the number and appointment before saving. Only confirm a demo booking after the local CRM write succeeds. No real employee is notified, no slot is checked against a real sales calendar, and no phone call is placed. Actual outbound calls retain the gateway's separate human-approval flow.

## Website snapshot

`voice/refurbo-inventory.json` holds 410 distinct product-card URLs captured across nine laptop category pages on 10 November 2026 UTC. Counts by page: 50, 50, 50, 50, 50, 50, 49, 50, 11. Collection includes rendered cards containing a title and the site's “Lowest Price” label, not a stock API or every individual product page.

89 cards showed a listed price and Add to Cart. 321 showed an approximate price and an enquiry action. Neither action proves current stock. Original titles and card text are retained with source URLs; normalized RAM and SSD fields are extracted from those titles, not guessed from model families. No account was used and no items were added to a cart.

The Lenovo ThinkPad T14 Gen 1 AMD product page was also checked: Ryzen 5 PRO 4650U, 16GB RAM, 256GB SSD, and ₹36,000 appeared on the page. Its policy blocks conflict: one-year warranty language and a separate six-month warranty claim, plus different delivery wording. The assistant must ask sales to confirm warranty, battery condition, delivery, upgrades, and return terms rather than turn conflicting marketing copy into a guarantee.

Sources:
- https://refurbo.in/laptop
- https://refurbo.in/product-details/buy-refurbished-lenovo-thinkpad-t14-uwkrbh

## Profile and activation

`voice/PROFILE.md` is the Refurbo-specific voice profile. It is limited to 6,000 characters by the existing loader. A small sourced shortlist is embedded so the profile can discuss some products even without tools. The full JSON inventory is not automatically included in the prompt.

Local browser demo setup uses these non-secret environment variables:

```bash
export VOICE_REFURBO_DEMO=true
export VOICE_PROFILE_PATH=/home/shravanrai/Git/hermes-voice-gateway/voice/PROFILE.md
export VOICE_GREETING="Hi, I'm Hermes, an AI assistant for this Refurbo demo. What kind of laptop are you looking for?"
export LOGURU_LEVEL=INFO
```

Use the project's verified Python environment with Pipecat exactly 1.7.0 to run `scripts/start_local_pipeline.py`, then open http://127.0.0.1:8765/. API keys belong in the user's local Hermes environment file, never in this document or a recording. Restart the single-call runner between calls.

The opt-in local demo is intended to expose `refurbo_search_inventory` and `refurbo_book_appointment`. Without those tools the profile must say it cannot save an appointment. This feature does not automatically deploy tools to the real Hermes/Twilio caller path.

Appointments are saved as plaintext SQLite records at `<HERMES_HOME>/plugin-data/hermes-voice-gateway/refurbo-demo.db` (normally `~/.hermes/plugin-data/hermes-voice-gateway/refurbo-demo.db`). `VOICE_REFURBO_DEMO_DB` overrides this path. The `appointments` table stores an ID, an idempotency key, and a JSON payload containing the confirmed requirements and appointment time. Booking requires positive numeric budget, RAM, and SSD targets; if the buyer is unsure, agree targets before booking rather than inventing values. No real sales calendar availability is checked, and rescheduling is not implemented.

## Recording story

1. Connect in the browser and hear the AI/demo disclosure.
2. Say: “I need a laptop for office work and some coding. My budget is forty thousand rupees.”
3. Answer preference and memory questions naturally, one at a time. For example, 16GB RAM and 256GB SSD, with no brand preference.
4. Hear a sourced option such as the ₹36,000 AMD ThinkPad T14 snapshot listing, subject to current price and stock confirmation.
5. Ask for more details from sales. Give a consenting test name and phone number, not another person's real data.
6. Choose an explicit future appointment time and confirm the date, time zone, number, and requirements.
7. Hear confirmation that the appointment was saved in the demo CRM and that no real team was notified.
8. Show the persisted CRM record. Do not display API keys, unrelated customer data, or private logs.

## Acceptance checks

- The profile loads completely without truncation.
- Inventory search uses the saved JSON and returns source URLs with snapshot caveats.
- Exact configurations stay distinct; missing SSD values are not treated as confirmed SSDs.
- Consent, a valid phone number, and a future timezone-aware date/time are required to book.
- A successful booking is persisted and read back; repeating the same request does not create duplicates.
- No Twilio call, real calendar booking, email, or Refurbo CRM update occurs.
- Existing non-demo behavior stays unchanged when `VOICE_REFURBO_DEMO` is unset.

Implementation and automated test results are reported separately; this document is not proof of a completed live voice call.

## Automated verification

The integrated suite passed with **118 tests and two existing Pipecat deprecation warnings**. Coverage includes real Pipecat pipeline construction with the opt-in tools, handler execution without provider network calls, search filtering, validation, SQLite persistence/readback, and duplicate-request handling. The profile also passes a complete-load check below the 6,000-character cap. A live microphone conversation and natural-language appointment tool selection still need a manual end-to-end check; automated construction and handler tests do not prove that call.
