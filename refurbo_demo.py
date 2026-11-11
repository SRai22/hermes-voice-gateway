"""Opt-in local-browser Refurbo demo plus a reusable offline inventory search.

Inventory is a stale snapshot, not a stock/policy guarantee. Demo appointments
are local SQLite records, not calendar bookings. The phone caller tools reuse
only the pure search helper; this module never notifies sales or dials a phone.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
import re

INVENTORY_PATH = Path(__file__).resolve().parent / "voice" / "refurbo-inventory.json"
INVENTORY_NOTICE = (
    "Snapshot only; availability and prices require sales confirmation. "
    "approximate_enquiry prices are approximate, not checkout offers. "
    "Do not guarantee warranty or return policies."
)


def search_inventory(*, model: str = "", budget_inr: int | None = None,
                     ram_gb: int | None = None, ssd_gb: int | None = None,
                     limit: int = 5, inventory_path: Path | None = None) -> dict:
    """Search offline model/title text with maximum budget and minimum RAM/SSD."""
    for name, value in (("budget_inr", budget_inr), ("ram_gb", ram_gb), ("ssd_gb", ssd_gb)):
        if value is not None and (type(value) is not int or value <= 0):
            raise ValueError(f"{name} must be a positive integer")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    if not isinstance(model, str) or len(model) > 200:
        raise ValueError("model must be text of at most 200 characters")
    snapshot = json.loads((inventory_path or INVENTORY_PATH).read_text(encoding="utf-8"))
    matches = []
    for product in snapshot["products"]:
        text = f"{product.get('model', '')} {product.get('title', '')}".casefold()
        if not all(token in text for token in model.casefold().split()):
            continue
        price, ram = product.get("price_inr"), product.get("ram_gb")
        capacity = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(GB|TB)\s*", str(product.get("ssd_capacity")), re.I)
        ssd = float(capacity[1]) * (1024 if capacity[2].upper() == "TB" else 1) if capacity else None
        if budget_inr is not None and (not isinstance(price, (int, float)) or price > budget_inr):
            continue
        if ram_gb is not None and (not isinstance(ram, (int, float)) or ram < ram_gb):
            continue
        if ssd_gb is not None and (ssd is None or ssd < ssd_gb):
            continue
        matches.append(product)
    matches.sort(key=lambda p: p.get("price_inr") if isinstance(p.get("price_inr"), (float, int)) else float("inf"))
    return dict(ok=True, source=snapshot.get("source"), captured_at=snapshot.get("captured_at"),
                total_matches=len(matches), products=matches[:limit], notice=INVENTORY_NOTICE)


SIMULATION_NOTICE = (
    "SIMULATION ONLY: saved locally; no actual team notified, no Twilio dial, "
    "no real calendar booking, and no guaranteed callback or availability."
)


def book_appointment(*, name: str, phone: str, consent: bool, appointment_at: str,
                     budget_inr: int, model: str, ram_gb: int, ssd_gb: int,
                     use_case: str, idempotency_key: str, db_path: Path | None = None) -> dict:
    """Validate, atomically save and read back a simulated sales callback request."""
    for field, value, maximum in (("name", name, 200), ("model", model, 200),
                                  ("use_case", use_case, 1000), ("idempotency_key", idempotency_key, 200)):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} must be nonempty text (max {maximum})")
    if not isinstance(phone, str) or not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
        raise ValueError("phone must be E164, for example +919876543210")
    if consent is not True:
        raise ValueError("explicit consent=true is required")
    for field, value in (("budget_inr", budget_inr), ("ram_gb", ram_gb), ("ssd_gb", ssd_gb)):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{field} must be a positive integer")
    try:
        when = datetime.fromisoformat(appointment_at)
    except (TypeError, ValueError):
        raise ValueError("appointment_at must be a timezone-aware ISO datetime") from None
    if when.tzinfo is None or when.utcoffset() is None or when <= datetime.now(timezone.utc):
        raise ValueError("appointment_at must be future and timezone-aware")
    payload = dict(name=name, phone=phone, consent=consent, appointment_at=appointment_at,
                   budget_inr=budget_inr, model=model, ram_gb=ram_gb, ssd_gb=ssd_gb,
                   use_case=use_case, idempotency_key=idempotency_key)
    encoded = json.dumps(payload, sort_keys=True)
    path = Path(db_path or os.environ.get("VOICE_REFURBO_DEMO_DB") or (
        Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
        / "plugin-data/hermes-voice-gateway/refurbo-demo.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS appointments "
                   "(idempotency_key TEXT PRIMARY KEY, id TEXT NOT NULL, payload TEXT NOT NULL)")
        db.execute("INSERT OR IGNORE INTO appointments VALUES (?, ?, ?)",
                   (idempotency_key, str(uuid.uuid4()), encoded))
        row = db.execute("SELECT id, payload FROM appointments WHERE idempotency_key = ?",
                         (idempotency_key,)).fetchone()
        if row[1] != encoded:
            raise ValueError("idempotency key already used with different appointment details")
    # Separate connection verifies committed persisted state, not just the write response.
    with sqlite3.connect(path) as db:
        persisted = db.execute("SELECT id, payload FROM appointments WHERE idempotency_key = ?",
                               (idempotency_key,)).fetchone()
    return dict(ok=True, simulation=True, team_notified=False, notice=SIMULATION_NOTICE,
                appointment={"id": persisted[0], **json.loads(persisted[1])})


def system_context() -> str:
    """Use a real local clock to resolve relative dates, then confirm the exact time."""
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds")
    return (
        f"\n\nRefurbo LOCAL DEMO. Current date/time in Asia/Kolkata: {now}. "
        f"{SIMULATION_NOTICE} {INVENTORY_NOTICE} "
        "Use refurbo_search_inventory before recommending specific stock/prices; "
        "model is token-matched against title/model, budget is maximum INR and RAM/SSD are minimum GB. "
        "Gather interest, model, RAM, SSD, budget and use case without inventing requirements. "
        "Then ask preferred callback date/time, name and E164 phone. Resolve relative dates "
        "using the clock above (default Asia/Kolkata), and confirm the exact date, time and timezone. "
        "Explain this is a simulated local record with no team notified; obtain explicit consent "
        "to save the details before calling refurbo_book_appointment. Never infer consent. "
        "Use a stable unique idempotency_key for each confirmed request and reuse it for retries. "
        "Report saved only after the tool returns ok=true, and repeat that no real callback is booked. "
        "These tools exist only in the opt-in local browser LLM, not Hermes chat or the real callback path."
    )


def register_tools(llm):
    """Register real Pipecat 1.7 tool handlers and return their context schema."""
    from pipecat.adapters.schemas.function_schema import FunctionSchema
    from pipecat.adapters.schemas.tools_schema import ToolsSchema

    positive = {"type": "integer", "minimum": 1}
    search_properties = {
        "model": {"type": "string", "maxLength": 200, "description": "Model/title tokens, e.g. ThinkPad T14. Omit for all models."},
        "budget_inr": {**positive, "description": "Maximum budget in INR."},
        "ram_gb": {**positive, "description": "Minimum RAM GB."},
        "ssd_gb": {**positive, "description": "Minimum SSD GB; 1TB = 1024GB."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
    }
    booking_properties = {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "phone": {"type": "string", "pattern": r"^\+[1-9][0-9]{7,14}$"},
        "consent": {"type": "boolean", "enum": [True], "description": "User explicitly agreed to saving this simulated request."},
        "appointment_at": {"type": "string", "format": "date-time", "description": "Confirmed future ISO datetime including UTC offset, normally +05:30."},
        "budget_inr": dict(positive),
        "model": {"type": "string", "minLength": 1, "maxLength": 200},
        "ram_gb": dict(positive),
        "ssd_gb": dict(positive),
        "use_case": {"type": "string", "minLength": 1, "maxLength": 1000},
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 200},
    }
    tools = []
    for name, function, properties, required, description in (
        ("refurbo_search_inventory", search_inventory, search_properties, [],
         "Search the local inventory snapshot; no live availability or policy guarantees."),
        ("refurbo_book_appointment", book_appointment, booking_properties, list(booking_properties),
         SIMULATION_NOTICE + " Requires explicit consent and confirmed requirements/date/time."),
    ):
        async def handler(params, _function=function, _allowed=frozenset(properties)):
            try:
                if set(params.arguments) - _allowed:
                    raise ValueError("Unknown tool argument")
                result = _function(**params.arguments)
            except (ValueError, TypeError) as exc:
                result = dict(ok=False, error=str(exc))
            except (OSError, sqlite3.Error):
                result = dict(ok=False, error="Local demo storage/inventory unavailable; nothing confirmed.")
            await params.result_callback(result)

        llm.register_function(name, handler)
        tools.append(FunctionSchema(name=name, description=description,
                                    properties=properties, required=required))
    return ToolsSchema(standard_tools=tools)
