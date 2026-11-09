"""Input schemas for the voice gateway tools.

Each schema is a JSON-schema-ish mapping describing what the LLM may pass to
the tool. Hermes validates tool calls against these schemas before invoking
the handler.
"""
from __future__ import annotations

PROPOSE_CALLBACK_SCHEMA = {
    "type": "object",
    "properties": {
        "destination": {
            "type": "string",
            "description": "Phone number to call back (E.164). Must be on the gateway's allowed list.",
        },
        "brief": {
            "type": "string",
            "description": "One sentence on why the call is made. The operator reads it before approving, and it is given to the agent as the call's purpose.",
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
            "description": "Twilio Call SID (CA followed by 32 hex characters). Give this or callback_id.",
        },
        "callback_id": {
            "type": "string",
            "description": "Id returned by voice_propose_callback. Reports the callback and its call.",
        },
    },
    "additionalProperties": False,
}

# No arguments: keys are read from ELEVENLABS_API_KEY / OPENAI_API_KEY in the
# environment so secrets never pass through the model or its transcript.
LOCAL_PIPELINE_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}
