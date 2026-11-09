"""Minimal Twilio REST client (stdlib only): place a call, fetch a call.

Blocking; callers run it with ``asyncio.to_thread``. Error text never contains credentials.
"""
from __future__ import annotations

import base64
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .twilio_util import scrub_numbers

API = "https://api.twilio.com/2010-04-01/Accounts"
CALL_SID_RE = re.compile(r"^CA[0-9a-fA-F]{32}$")
TIMEOUT = 15


class TwilioError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(scrub_numbers(message))
        self.status = status


def _http(method: str, url: str, account_sid: str, auth_token: str, form: dict | None = None) -> dict:
    data = urllib.parse.urlencode(form).encode() if form else None
    request = urllib.request.Request(url, data=data, method=method)
    basic = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    request.add_header("Authorization", f"Basic {basic}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read() or b"{}").get("message", "")
        except ValueError:
            message = ""
        raise TwilioError(exc.code, message or f"Twilio returned HTTP {exc.code}") from None
    except (OSError, http.client.HTTPException):  # URLError, timeouts, resets, dropped connections
        raise TwilioError(0, "could not reach Twilio") from None
    except ValueError:  # 2xx with a body that is not JSON
        raise TwilioError(0, "Twilio sent an unreadable response") from None


def create_call(account_sid: str, auth_token: str, *, to: str, from_: str, url: str) -> dict:
    """Start an outbound call; Twilio fetches TwiML from ``url`` when the callee answers."""
    form = {"To": to, "From": from_, "Url": url, "Method": "POST", "Timeout": "45"}
    return _http("POST", f"{API}/{account_sid}/Calls.json", account_sid, auth_token, form)


def fetch_call(account_sid: str, auth_token: str, call_sid: str) -> dict:
    if not CALL_SID_RE.match(call_sid):
        raise ValueError("call_sid must look like CA followed by 32 hex characters")
    return _http("GET", f"{API}/{account_sid}/Calls/{call_sid}.json", account_sid, auth_token)
