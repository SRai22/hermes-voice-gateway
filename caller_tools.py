"""Restricted phone tools: trusted-turn leases, snapshot search and enquiries.

No Hermes imports at load time. Caller/model arguments never choose ownership,
file paths, or permissions; the gateway binds the source and grants a live turn.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

from . import callbacks, refurbo_demo

logger = logging.getLogger(__name__)

TOOLSET = "voice_caller"
TOOLSET_SELECTION = [TOOLSET, "no_mcp"]
TOOL_NAMES = frozenset({"voice_search_inventory", "voice_save_enquiry"})
SEARCH_FIELDS = frozenset({"model", "budget_inr", "ram_gb", "ssd_gb", "limit"})
ENQUIRY_FIELDS = frozenset({"name", "requirements", "consent", "model", "budget_inr", "ram_gb", "ssd_gb"})
ENQUIRY_NOTICE = (
    "Saved locally for operator review. No team has been notified; "
    "no callback is approved or scheduled."
)
CALLER_INSTRUCTIONS = (
    "Phone tools are limited to voice_search_inventory and voice_save_enquiry. "
    "Search the saved snapshot before recommending specific laptops/prices; disclose that sales must confirm price and stock. "
    "Collect name and requirements, leave unknown budget/RAM/SSD/model fields out, and read back the details. "
    "Explain that saving records the current caller number locally for operator review, "
    "does not notify a team, and does not approve or schedule a callback. "
    "Ask for explicit consent to saving those details before voice_save_enquiry; never infer consent. "
    "Only confirm saved after ok=true. One enquiry per call; retry unchanged details if necessary."
)

_GRANTS_SCHEMA = """CREATE TABLE IF NOT EXISTS voice_caller_grants (
    session_key TEXT PRIMARY KEY, caller TEXT NOT NULL, call_sid TEXT NOT NULL,
    message_id TEXT NOT NULL, expires_at REAL NOT NULL)"""
_ENQUIRIES_SCHEMA = """CREATE TABLE IF NOT EXISTS voice_enquiries (
    id TEXT PRIMARY KEY, call_sid TEXT NOT NULL UNIQUE, caller TEXT NOT NULL,
    payload TEXT NOT NULL, created_at REAL NOT NULL)"""


@dataclass(frozen=True)
class CallerIdentity:
    session_key: str
    caller: str
    call_sid: str
    message_id: str

    def values(self) -> tuple[str, str, str, str]:
        return self.session_key, self.caller, self.call_sid, self.message_id


_live_turns: dict[CallerIdentity, Callable[[], bool]] = {}
_live_lock = threading.RLock()


def grant_turn(identity: CallerIdentity, *, ttl: float, is_active: Callable[[], bool]) -> None:
    """Require both a process-local live call and a profile-scoped database lease."""
    with open_caller_store() as store:
        store.grant(identity, ttl=ttl)
    with _live_lock:
        _live_turns[identity] = is_active


def revoke_turn(identity: CallerIdentity) -> None:
    # Invalidate locally first: a failed SQLite revoke must not permit tools.
    with _live_lock:
        _live_turns.pop(identity, None)
    with open_caller_store() as store:
        store.revoke(identity)


def _require_live(identity: CallerIdentity) -> None:
    with _live_lock:
        active = _live_turns.get(identity)
        if active is None or not active():
            raise PermissionError("phone turn is not live in this gateway process")


def current_identity() -> CallerIdentity:
    """Require task-bound gateway context, never a model argument or env-only id."""
    try:
        from gateway.session_context import bound_session_key, get_session_env
    except ImportError:
        raise PermissionError("live caller context is unavailable") from None
    identity = CallerIdentity(
        bound_session_key(), get_session_env("HERMES_SESSION_CHAT_ID"),
        get_session_env("HERMES_SESSION_THREAD_ID"), get_session_env("HERMES_SESSION_MESSAGE_ID"),
    )
    if get_session_env("HERMES_SESSION_PLATFORM") != "voice" or not all(identity.values()):
        raise PermissionError("a live phone turn is required")
    return identity


def is_voice_context() -> bool:
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return False
    return get_session_env("HERMES_SESSION_PLATFORM") == "voice"


class CallerStore:
    def __init__(self, conn: sqlite3.Connection, clock=time.time):
        self._db, self._clock = conn, clock
        conn.execute(_GRANTS_SCHEMA)
        conn.execute(_ENQUIRIES_SCHEMA)
        conn.commit()

    def grant(self, identity: CallerIdentity, *, ttl: float) -> None:
        """The adapter alone issues a bounded lease before admitting a turn."""
        if not all(identity.values()) or ttl <= 0:
            raise ValueError("a complete caller identity and positive lease are required")
        now = self._clock()
        with self._db:
            cur = self._db.execute(
                "INSERT INTO voice_caller_grants VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(session_key) DO UPDATE SET caller=excluded.caller, "
                "call_sid=excluded.call_sid, message_id=excluded.message_id, expires_at=excluded.expires_at "
                "WHERE voice_caller_grants.expires_at<=? OR "
                "(voice_caller_grants.caller=excluded.caller AND voice_caller_grants.call_sid=excluded.call_sid "
                "AND voice_caller_grants.message_id=excluded.message_id)",
                (*identity.values(), now + ttl, now),
            )
            if cur.rowcount != 1:
                raise PermissionError("another phone turn owns this session")

    def authorize(self, identity: CallerIdentity) -> None:
        _require_live(identity)
        row = self._db.execute(
            "SELECT 1 FROM voice_caller_grants WHERE session_key=? AND caller=? "
            "AND call_sid=? AND message_id=? AND expires_at>?",
            (*identity.values(), self._clock()),
        ).fetchone()
        if row is None:
            raise PermissionError("phone turn permission expired or is no longer active")

    def revoke(self, identity: CallerIdentity) -> None:
        with self._db:
            self._db.execute(
                "DELETE FROM voice_caller_grants WHERE session_key=? AND caller=? AND call_sid=? AND message_id=?",
                identity.values(),
            )

    def save(self, identity: CallerIdentity, payload: dict) -> str:
        """Authorize and commit together so hangup cannot race a later write."""
        encoded = json.dumps(payload, sort_keys=True)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            self.authorize(identity)
            row = self._db.execute(
                "SELECT id, caller, payload FROM voice_enquiries WHERE call_sid=?", (identity.call_sid,),
            ).fetchone()
            if row is not None:
                if row[1:] != (identity.caller, encoded):
                    raise ValueError("this call already has an enquiry with different details; ask an operator to amend it")
            else:
                self._db.execute(
                    "INSERT INTO voice_enquiries VALUES (?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, identity.call_sid, identity.caller, encoded, self._clock()),
                )
        persisted = self._db.execute(
            "SELECT id FROM voice_enquiries WHERE call_sid=? AND caller=? AND payload=?",
            (identity.call_sid, identity.caller, encoded),
        ).fetchone()
        if persisted is None:
            raise sqlite3.DatabaseError("enquiry readback failed")
        return persisted[0]


@contextmanager
def open_caller_store():
    """Use the same profile-scoped plugin DB/connection lifecycle as callbacks."""
    conn = callbacks._connect()
    try:
        yield CallerStore(conn)
    finally:
        callbacks._release(conn)


def require_access() -> CallerIdentity:
    identity = current_identity()
    with open_caller_store() as store:
        store.authorize(identity)
    return identity


def verify_tool_surface() -> None:
    """Fail closed when Hermes' effective selection adds any other capability."""
    from agent.skill_utils import parse_config_string_list
    from gateway.session_context import bound_session_key, get_session_env
    from hermes_cli.config import load_config
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions

    if not callable(bound_session_key) or not callable(get_session_env):
        raise PermissionError("unsupported gateway caller context")
    config = load_config()
    selected = {**config, "platform_toolsets": {**(config.get("platform_toolsets") or {}), "voice": TOOLSET_SELECTION}}
    enabled = sorted(_get_platform_tools(selected, "voice"))
    disabled = parse_config_string_list((config.get("agent") or {}).get("disabled_toolsets")) or None
    definitions = get_tool_definitions(enabled_toolsets=enabled, disabled_toolsets=disabled, quiet_mode=True)
    if {tool["function"]["name"] for tool in definitions} != TOOL_NAMES:
        logger.error(
            "[voice] caller tool policy is not isolated; exclude other plugin toolsets for voice "
            "and disable context-engine additions/tool-search deferral before accepting caller turns"
        )
        raise PermissionError("caller tool policy must resolve to inventory search and save enquiry only")


def search_inventory(args: dict, **kwargs) -> str:
    """Search the packaged snapshot only, with no caller-controlled path."""
    try:
        require_access()
        if not isinstance(args, dict) or set(args) - SEARCH_FIELDS:
            raise ValueError("unknown inventory search argument")
        if "limit" in args and (type(args["limit"]) is not int or not 1 <= args["limit"] <= 5):
            raise ValueError("limit must be between 1 and 5")
        return json.dumps(refurbo_demo.search_inventory(**args))
    except (PermissionError, ValueError, TypeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    except (OSError, sqlite3.Error):
        return json.dumps({"ok": False, "error": "caller inventory/storage unavailable"})


def save_enquiry(args: dict, **kwargs) -> str:
    """Save one consented enquiry per call; retries cannot create duplicates."""
    try:
        identity = current_identity()
        if not isinstance(args, dict) or set(args) - ENQUIRY_FIELDS:
            raise ValueError("unknown enquiry argument")
        if args.get("consent") is not True:
            raise ValueError("explicit consent=true is required to save the enquiry")
        for name, maximum in (("name", 200), ("requirements", 1000), ("model", 200)):
            value = args.get(name)
            if name == "model" and name not in args:
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"{name} must be nonempty text (max {maximum})")
        for name in ("budget_inr", "ram_gb", "ssd_gb"):
            if name in args and (type(args[name]) is not int or args[name] <= 0):
                raise ValueError(f"{name} must be a positive integer when known")
        with open_caller_store() as store:
            enquiry_id = store.save(identity, args)
        return json.dumps({
            "ok": True, "enquiry_id": enquiry_id, "status": "saved_for_operator_review",
            "team_notified": False, "callback_approved": False, "notice": ENQUIRY_NOTICE,
        })
    except (PermissionError, ValueError, TypeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)})
    except (OSError, sqlite3.Error):
        return json.dumps({"ok": False, "error": "enquiry storage unavailable; nothing confirmed"})
