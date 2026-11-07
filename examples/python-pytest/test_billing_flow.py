"""How an application's test suite uses the emulator.

Run the emulator first (docker or `saci`), then:

    ASAAS_BASE_URL=http://localhost:8000 pytest examples/python-pytest

Nothing here is specific to Python: the same requests work from any language.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import httpx
import pytest

BASE_URL = os.environ.get("ASAAS_BASE_URL", "http://localhost:8000")


@pytest.fixture
def asaas():
    """A client for the emulator, reset to a clean, frozen state before each test."""
    with httpx.Client(base_url=BASE_URL, headers={"access_token": "$aact_example"}) as client:
        client.post("/_saci/reset")
        client.post("/_saci/clock", json={"now": "2026-03-02T09:00:00"})
        yield client


def charge(asaas: httpx.Client, value: float, billing_type: str = "PIX", days: int = 5) -> dict:
    customer = asaas.post("/v3/customers", json={"name": "Maria", "cpfCnpj": "52998224725"})
    return asaas.post(
        "/v3/payments",
        json={
            "customer": customer.json()["id"],
            "billingType": billing_type,
            "value": value,
            "dueDate": (date(2026, 3, 2) + timedelta(days=days)).isoformat(),
        },
    ).json()


def test_paid_charge_funds_a_payout(asaas: httpx.Client):
    payment = charge(asaas, 101.99)
    asaas.post(f"/v3/sandbox/payment/{payment['id']}/confirm")
    assert asaas.get("/v3/finance/balance").json()["balance"] == 100.0  # after the 1.99 fee

    payout = asaas.post(
        "/v3/transfers",
        json={"value": 60, "pixAddressKey": "owner@example.com", "pixAddressKeyType": "EMAIL"},
    ).json()
    assert payout["status"] == "DONE"
    assert asaas.get("/v3/finance/balance").json()["balance"] == 40.0


def test_payout_without_funds_is_refused(asaas: httpx.Client):
    response = asaas.post(
        "/v3/transfers",
        json={"value": 10, "pixAddressKey": "owner@example.com", "pixAddressKeyType": "EMAIL"},
    )
    assert response.status_code == 400


def test_unpaid_charge_goes_overdue_when_time_passes(asaas: httpx.Client):
    payment = charge(asaas, 50, days=1)
    asaas.post("/_saci/clock", json={"advanceDays": 2})
    assert asaas.get(f"/v3/payments/{payment['id']}/status").json() == {"status": "OVERDUE"}
    events = [e["event"] for e in asaas.get("/_saci/events").json()["data"]]
    assert events == ["PAYMENT_CREATED", "PAYMENT_OVERDUE"]
