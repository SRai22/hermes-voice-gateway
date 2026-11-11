# What this project is

This is a plugin for Hermes, an AI assistant. Think of Hermes as a smart helper you can talk to, and this plugin as giving Hermes the ability to answer phone calls on a business line.

Right now, if a customer calls a business phone number, a person has to pick up. With this plugin, Hermes can answer the call, listen to what the caller needs, look up information (like an order status), and even offer to call the person back — but only after a human operator approves the callback and the phone number it should go to.

The plugin lives alongside Hermes and connects to:
- Twilio, which handles the actual phone calls and audio
- ElevenLabs, which turns speech into text and text back into speech
- OpenAI, which powers the language model in the conversation
- Pipecat, which manages the back-and-forth conversation flow

No audio is saved locally, and Hermes only uses tools the business has explicitly allowed.

## What is already done

The basic structure of the plugin is in place. Hermes can load it, see that it offers three phone-related tools (answer a call, propose a callback, check a call's status), and knows to check that the phone and speech services are configured before letting anyone use those tools. The plugin has passed Hermes's own validation and registration checks.

## What comes next

The full Pipecat pipeline is now wired: WebRTC audio in → VAD turn detection → ElevenLabs STT → OpenAI LLM → ElevenLabs TTS → WebRTC audio out, with RTVI for client-server control messages. The pipeline starts as a background task and waits for a WebRTC client to connect.

A browser-based voice UI is in ``frontend/`` (``index.html`` + ``signaling-server.py``). Start the signaling server, open the HTML page, and you can speak into the pipeline end to end — no phone number needed.

Model names come from environment variables (defaults in parentheses):
- ``ELEVENLABS_STT_MODEL`` (scribe_v2) — ElevenLabs speech-to-text
- ``ELEVENLABS_TTS_MODEL`` (eleven_flash_v2_5) — ElevenLabs text-to-speech
- ``OPENAI_MODEL`` (gpt-5.6-luna) — OpenAI LLM

Set these plus ``ELEVENLABS_API_KEY`` and ``OPENAI_API_KEY`` in ``~/.hermes/.env`` before starting the pipeline.

Each piece will be tested before moving to the next, so nothing is left broken or half-finished.
