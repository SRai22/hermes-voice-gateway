# Hermes Voice Gateway

**A Pipecat-powered plugin that connects Hermes to business phone lines.**

Connect a Twilio Voice number to Hermes so it can answer callers, act through the tools a business already uses, and make operator-approved callbacks. Start with teams already using Twilio Voice, especially service and operations teams that handle repeat calls about orders, bookings, and support cases.

## What a business can do

- **Answer inbound calls.** A caller asks about a late delivery. Hermes checks the order using a permitted tool and opens a support case if the issue needs follow-up.
- **Call back from the business number.** A staff member asks Hermes to propose a callback with a destination and a brief. The operator reviews both and confirms the destination before the gateway places the call. Hermes can check the provider's call status afterward.
- **Use the same workflow across channels.** A phone call starts a case that staff can pick up in Hermes chat. A future desk speaker could surface that same case and request an action through the employee's permissions.

Existing Hermes integrations already offer live calling and post-call actions. The opportunity is to make a business's own phone line an entry point into its existing workflows, with a self-hostable voice service and separate permissions for callers and staff. The adoption signal is whether a team routes a recurring call workflow through the gateway again after the first week. [Inkbox marketplace listing](https://hermes-agent.nousresearch.com/marketplace/inkbox)

## How it works

| Component | Role |
| --- | --- |
| **Twilio Voice** | Routes inbound and outbound calls and streams audio in both directions. |
| **Pipecat and Deepgram** | Pipecat runs the real-time audio pipeline and handles turn-taking and interruptions. Deepgram handles speech recognition and synthesis with one key. |
| **Hermes caller profile** | Receives conversation text and can use a limited set of public-facing business tools. |
| **Hermes operator plugin** | Proposes callbacks and reads status. A separate operator console handles approval and dialing. |

The gateway verifies Twilio signatures and binds the media connection to the call. The operator database stores callback numbers, briefs, and approval status. The gateway keeps call audio out of local storage. Hermes may retain conversation text according to the caller profile's session settings. [Pipecat Twilio examples](https://github.com/pipecat-ai/pipecat-examples/tree/main/twilio-chatbot) · [Hermes API server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server)

## Where it can go

- **More business phone systems.** Add other providers and SIP support so teams can keep their existing phone setup.
- **Browsers and desk terminals.** Add a WebRTC voice endpoint for staff on a laptop, tablet, or dedicated device. [Pipecat transports](https://docs.pipecat.ai/client/concepts/choosing-a-transport)
- **Alexa and open hardware.** A stock Echo could invoke Hermes through an Alexa custom skill, with Alexa handling the spoken interaction. Home Assistant Assist offers a separate route for a dedicated “Hey Hermes” device. A community project has already connected modified Echo devices to Hermes through Assist. [Alexa Skills Kit](https://developer.amazon.com/en-US/docs/alexa/devconsole/build-your-skill.html) · [Home Assistant Assist](https://www.home-assistant.io/voice_control/) · [Community Echo project](https://github.com/majorminorlabs/hermes-voice)

## Hackathon demo

Use a real Twilio test line and a consenting tester. Show the plugin working in about **82 seconds**.

| Time | Show |
| --- | --- |
| 0–8 s | Introduce Hermes Voice Gateway and show it loaded in Hermes. |
| 8–35 s | Dial the test line, report a missed delivery, and show the case appear in the connected test system. |
| 35–65 s | Have Hermes propose a callback. Review and approve the exact destination, then show the phone ring. |
| 65–82 s | Show the actual call status in Hermes and close on the project name. |

**Submission deadline is Wednesday, November 11, 2026 at 2 p.m. Pacific (5 p.m. Eastern).** The organizers judge usefulness, reliability, polish, and creativity. [Organizer announcement](https://teapaw.org/NousResearch/status/2118094256438936062)

1. Open a PR that adds `plugin-catalog/hermes-voice-gateway.yaml` to `NousResearch/hermes-agent`. Point it to a public plugin repository and pin the exact validated 40-character commit SHA. Include the capability and data disclosures. [Catalog submission guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission)
2. Post a video under 90 seconds on X, tag `@NousResearch`, and show the working plugin.
3. Submit the PR and X post links through the entry form. You can submit while the PR is open. [Official entry form](https://form.typeform.com/to/FwJLvOln)

## Current state

The local prototype implements signed inbound routing, callback proposals, one-time operator approval, and provider status lookup. Four tests passed with simulated providers, and Hermes's validator passed the manifest, tool registration, and security scan checks. The validator's dependency preparation and the live call still need verification. The recording, public repository and catalog PR, X post, and form submission are pending.

Start live setup with a Twilio Voice test number, a Deepgram key, a dedicated Hermes voice profile, and a public HTTPS endpoint for the voice service. Then run the live call and callback before recording. [Twilio Voice setup](https://www.twilio.com/docs/usage/trials/try-out-voice) · [Deepgram API keys](https://developers.deepgram.com/guides/fundamentals/authenticating)

[Plugin setup and disclosures](hermes-voice-gateway/README.md) · [Demo checklist](hermes-voice-gateway-submission/demo.md) · [Catalog entry template](hermes-voice-gateway-submission/hermes-voice-gateway.yaml.template) · [PR draft](hermes-voice-gateway-submission/catalog-pr.md)
