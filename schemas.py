"""Input schemas for the voice gateway tools.

Each schema is a JSON-schema-ish mapping describing what the LLM may pass to
the tool. Hermes validates tool calls against these schemas before invoking
the handler.
"""
from __future__ import annotations

ROUTE_INBOUND_SCHEMA = {
    "type": "object",
    "properties": {
        "call_sid": {
            "type": "string",
            "description": "Twilio Call SID of the inbound call to answer.",
        },
        "to": {
            "type": "string",
            "description": "Business number the caller reached (E.164).",
        },
        "from": {
            "type": "string",
            "description": "Caller's number (E.164).",
        },
    },
    "required": ["call_sid"],
    "additionalProperties": False,
}

PROPOSE_CALLBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {
            "type": "string",
            "description": "Phone number to call back (E.164).",
        },
        "brief": {
            "type": "string",
            "description": "Short reason for the callback the operator approves.",
        },
        "staff_member": {
            "type": "string",
            "description": "Staff member who asked Hermes to propose the callback.",
        },
    },
    "required": ["destination", "brief"],
    "additionalProperties": False,
}

CHECK_STATUS_SCHEMA = {
    "type": "object",
    "properties": {
        "call_sid": {
            "type": "string",
            "description": "Call SID whose provider status to look up.",
        },
    },
    "required": ["call_sid"],
    "additionalProperties": False,
}

LOCAL_PIPELINE_SCHEMA = {
    "type": "object",
    "properties": {
        "elevenlabs_api_key": {
            "type": "string",
            "description": "ElevenLabs API key for STT/TTS. Falls back to the ELEVENLABS_API_KEY env var if omitted.",
        },
        "openai_api_key": {
            "type": "string",
            "description": "OpenAI API key for the LLM. Falls back to the OPENAI_API_KEY env var if omitted.",
        },
    },
    "required": ["elevenlabs_api_key", "openai_api_key"],
    "additionalProperties": False,
}
