"""Pure Twilio helpers: signature check, TwiML, tokens, allowlist."""
from __future__ import annotations

import base64
import hashlib
import hmac
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import twilio_util as t  # noqa: E402

TOKEN = "auth-token"
URL = "https://example.test/voice"
PARAMS = {"From": "+15550001111", "CallSid": "CA123", "To": "+15552223333"}


def _sign(url: str, params: dict, token: str = TOKEN) -> str:
    data = url + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()


def test_valid_signature_passes() -> None:
    assert t.validate_signature(TOKEN, URL, PARAMS, _sign(URL, PARAMS))


def test_tampered_params_or_wrong_token_fail() -> None:
    sig = _sign(URL, PARAMS)
    assert not t.validate_signature(TOKEN, URL, {**PARAMS, "From": "+19990000000"}, sig)
    assert not t.validate_signature("other", URL, PARAMS, sig)


def test_empty_token_or_signature_fail_closed() -> None:
    assert not t.validate_signature("", URL, PARAMS, _sign(URL, PARAMS, ""))
    assert not t.validate_signature(TOKEN, URL, PARAMS, "")


def test_default_port_variant_is_accepted() -> None:
    signed_with_port = "https://example.test:443/voice"
    assert t.validate_signature(TOKEN, URL, PARAMS, _sign(signed_with_port, PARAMS))


def test_twiml_escapes_attribute_values() -> None:
    xml = t.twiml_connect_stream("wss://h/media", {"token": 'a"b<c'})
    assert "<Connect><Stream" in xml
    assert 'a"b<c' not in xml  # quoted/escaped, cannot break out of the attribute


def test_stream_token_is_one_time_and_expires() -> None:
    tokens = t.StreamTokens(ttl_seconds=60)
    tok = tokens.issue({"caller": "+1"})
    assert tokens.redeem(tok) == {"caller": "+1"}
    assert tokens.redeem(tok) is None
    assert tokens.redeem("nope") is None
    short = t.StreamTokens(ttl_seconds=0.01)
    tok = short.issue({"caller": "+1"})
    time.sleep(0.03)
    assert short.redeem(tok) is None


def test_allowlist_and_redaction() -> None:
    assert t.parse_allowed(" +1555, ,+1666 ") == {"+1555", "+1666"}
    assert t.redact_phone("+15550001111") == "***1111"
    assert "5550" not in t.redact_phone("+15550001111")
