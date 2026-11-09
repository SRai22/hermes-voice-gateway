"""Pure Twilio helpers: webhook signature check, TwiML, allowlist, one-time stream tokens.

No Hermes, Pipecat or Twilio SDK imports, so it is unit-testable on its own.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
import urllib.parse
from xml.sax.saxutils import quoteattr


def redact_phone(number: str) -> str:
    """Keep only the last 4 digits for logs."""
    return "***" + number[-4:] if len(number) > 4 else "***"


_NUMBER_RE = re.compile(r"\+?\d[\d\s().-]{6,}\d")


def scrub_numbers(text: str) -> str:
    """Replace anything that looks like a phone number (Twilio errors can echo them)."""
    return _NUMBER_RE.sub("[number]", text)


def parse_allowed(csv: str) -> set[str]:
    return {n.strip() for n in csv.split(",") if n.strip()}


def _port_variant(url: str) -> str | None:
    """URL with the scheme's default port added or stripped; None for other ports."""
    p = urllib.parse.urlparse(url)
    default = {"https": 443, "http": 80}.get(p.scheme)
    if default is None or p.hostname is None:
        return None
    if p.port == default:
        netloc = p.hostname
    elif p.port is None:
        netloc = f"{p.hostname}:{default}"
    else:
        return None
    return urllib.parse.urlunparse(p._replace(netloc=netloc))


def _signature_ok(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    data = url + "".join(k + params[k] for k in sorted(params))
    mac = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1)
    return hmac.compare_digest(base64.b64encode(mac.digest()), signature.encode())


def validate_signature(auth_token: str, url: str, params: dict[str, str], signature: str) -> bool:
    """Check ``X-Twilio-Signature`` (HMAC-SHA1). Fails closed on an empty token or signature.

    Twilio may sign the URL with or without the default port, so both are tried.
    """
    if not auth_token or not signature:
        return False
    if _signature_ok(auth_token, url, params, signature):
        return True
    variant = _port_variant(url)
    return bool(variant and _signature_ok(auth_token, variant, params, signature))


def twiml_connect_stream(ws_url: str, parameters: dict[str, str]) -> str:
    """TwiML that opens a bidirectional Media Stream to ``ws_url``."""
    params = "".join(f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in parameters.items())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={quoteattr(ws_url)}>{params}</Stream></Connect></Response>"
    )


def twiml_reject(message: str) -> str:
    """Say a short message, then hang up."""
    from xml.sax.saxutils import escape

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Say>{escape(message)}</Say><Hangup/></Response>"
    )


class StreamTokens:
    """One-time, short-lived tokens that tie a media WebSocket to a verified /voice webhook.

    Twilio's custom <Parameter> values come back in the WebSocket ``start`` message, so a
    token issued after the signature check proves the stream belongs to that call.
    """

    def __init__(self, ttl_seconds: float = 60.0):
        self._ttl = ttl_seconds
        self._pending: dict[str, tuple[float, dict[str, str]]] = {}

    def issue(self, info: dict[str, str]) -> str:
        now = time.monotonic()
        self._pending = {t: v for t, v in self._pending.items() if v[0] > now}  # drop expired
        token = secrets.token_urlsafe(24)
        self._pending[token] = (now + self._ttl, info)
        return token

    def pending(self) -> list[dict[str, str]]:
        """Info of tokens issued and not yet redeemed or expired."""
        now = time.monotonic()
        return [info for exp, info in self._pending.values() if exp > now]

    def redeem(self, token: str) -> dict[str, str] | None:
        entry = self._pending.pop(token or "", None)
        if entry is None or entry[0] <= time.monotonic():
            return None
        return entry[1]
