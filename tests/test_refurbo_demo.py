"""Offline Refurbo demo: no vendor, calendar, or telephony calls."""
import json
import sys
from typing import Any
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_search_filters_local_inventory(tmp_path):
    import refurbo_demo as demo

    path = tmp_path / "inventory.json"
    products = [
        dict(title="ThinkPad T14 office laptop", model="T14", ram_gb=16,
             ssd_capacity="1TB", price_inr=36000, price_type="approximate_enquiry",
             purchase_action="enquire", url="https://refurbo.in/t14"),
        dict(title="ThinkPad T14", model="T14", ram_gb=8,
             ssd_capacity="256GB", price_inr=24000, price_type="listed",
             purchase_action="add_to_cart", url="https://refurbo.in/t14-small"),
    ]
    path.write_text(json.dumps(dict(source="https://refurbo.in", captured_at="snapshot", products=products)))
    result = demo.search_inventory(model="thinkpad t14", budget_inr=40000,
                                   ram_gb=16, ssd_gb=512, inventory_path=path)
    assert result["ok"] is True
    assert result["total_matches"] == 1
    assert result["products"] == [products[0]]
    assert result["captured_at"] == "snapshot"
    assert "approximate" in result["notice"].lower()
    assert "warranty" in result["notice"].lower()


def booking_args() -> dict[str, Any]:
    return dict(name="Test Buyer", phone="+919876543210", consent=True,
                appointment_at=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                budget_inr=40000, model="T14", ram_gb=16, ssd_gb=512,
                use_case="Office work", idempotency_key="test-request-1")


def test_booking_persisted_readback_idempotency_and_default_path(tmp_path, monkeypatch):
    import refurbo_demo as demo

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    args = booking_args()
    first = demo.book_appointment(**args)
    assert first["ok"] and first["simulation"] is True
    assert first["appointment"]["phone"] == args["phone"]
    assert first["appointment"]["use_case"] == "Office work"
    assert first["team_notified"] is False
    assert "no real" in first["notice"].lower()
    assert demo.book_appointment(**args)["appointment"] == first["appointment"]
    assert (tmp_path / "plugin-data/hermes-voice-gateway/refurbo-demo.db").exists()
    with pytest.raises(ValueError, match="idempotency"):
        demo.book_appointment(**{**args, "model": "different"})


@pytest.mark.parametrize("field,value", [
    ("name", " "), ("phone", "9876543210"), ("consent", False),
    ("consent", "true"), ("appointment_at", "2020-01-01T12:00:00+05:30"),
    ("appointment_at", "2099-01-01T12:00:00"), ("budget_inr", 0),
    ("model", ""), ("ram_gb", True), ("ssd_gb", -1), ("use_case", ""),
    ("idempotency_key", ""),
])
def test_invalid_booking_never_creates_database(tmp_path, field, value):
    import refurbo_demo as demo

    path = tmp_path / "invalid.db"
    with pytest.raises(ValueError):
        demo.book_appointment(**{**booking_args(), field: value}, db_path=path)
    assert not path.exists()


def test_distribution_contains_demo_module_and_inventory():
    import tomllib

    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "pyproject.toml").read_text())
    includes = config["tool"]["hatch"]["build"]["only-include"]
    assert "refurbo_demo.py" in includes
    assert "voice/refurbo-inventory.json" in includes


@pytest.mark.parametrize("kwargs", [
    {"budget_inr": -1}, {"ram_gb": True}, {"ssd_gb": "512"},
    {"limit": 21}, {"model": None},
])
def test_search_rejects_invalid_constraints(kwargs):
    import refurbo_demo as demo

    with pytest.raises(ValueError):
        demo.search_inventory(**kwargs)


def test_search_real_snapshot_handles_unknown_ssd_and_no_matches():
    import refurbo_demo as demo

    assert demo.search_inventory(model="nonexistent-model-zzzz")["total_matches"] == 0
    result = demo.search_inventory(budget_inr=40000, ram_gb=16, ssd_gb=256, limit=20)
    assert result["products"]
    assert all(p["price_inr"] <= 40000 and p["ram_gb"] >= 16 and p["ssd_capacity"]
               for p in result["products"])
