"""Contract tests that must pass against the emulator AND the real Asaas sandbox.

By default they run against an in-process emulator. Point them at the sandbox with:

    ASAAS_CONFORMANCE_BASE_URL=https://api-sandbox.asaas.com \
    ASAAS_CONFORMANCE_API_KEY='$aact_...' \
    pytest -m conformance

They only assert what the documentation promises and what both sides must agree on.
When the sandbox disagrees with the emulator, this suite is where it shows up first,
and the emulator is the side that gets fixed. A fake that lies is worse than no fake.

The sandbox is shared, stateful and slow, so every test creates its own data and
compares balances as deltas instead of absolute values.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from saci.app import create_app
from saci.config import Settings

pytestmark = pytest.mark.conformance

BASE_URL = os.environ.get("ASAAS_CONFORMANCE_BASE_URL")
API_KEY = os.environ.get("ASAAS_CONFORMANCE_API_KEY", "$aact_conformance")
AGAINST_SANDBOX = bool(BASE_URL)
CPF = "52998224725"


@pytest.fixture(scope="module")
def api() -> Iterator[httpx.Client]:
    if AGAINST_SANDBOX:
        with httpx.Client(
            base_url=BASE_URL, headers={"access_token": API_KEY}, timeout=30
        ) as client:
            yield client
    else:
        app = create_app(Settings(delivery_mode="manual"))
        with TestClient(app, headers={"access_token": API_KEY}) as client:
            yield client


def due(days: int = 7) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


@pytest.fixture(scope="module")
def customer(api: httpx.Client) -> dict:
    response = api.post(
        "/v3/customers",
        json={
            "name": "Conformance Test",
            "cpfCnpj": CPF,
            "externalReference": f"conformance-{uuid.uuid4()}",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def balance(api: httpx.Client) -> float:
    return api.get("/v3/finance/balance").json()["balance"]


def test_invalid_key_is_401_with_error_list(api: httpx.Client):
    response = api.get("/v3/customers", headers={"access_token": "$aact_definitely_invalid"})
    if not AGAINST_SANDBOX:
        pytest.skip("the emulator accepts any key unless SACI_API_KEYS is set")
    assert response.status_code == 401
    assert "errors" in response.json()


def test_missing_key_is_401(api: httpx.Client):
    response = api.get("/v3/customers", headers={"access_token": ""})
    assert response.status_code == 401


def test_customer_shape(customer: dict):
    assert customer["object"] == "customer"
    assert customer["id"].startswith("cus_")
    assert customer["personType"] == "FISICA"
    assert customer["cpfCnpj"] == CPF
    assert customer["deleted"] is False


def test_invalid_document_is_400(api: httpx.Client):
    response = api.post("/v3/customers", json={"name": "Bad", "cpfCnpj": "12345678900"})
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"]


def test_list_envelope(api: httpx.Client, customer: dict):
    page = api.get("/v3/customers", params={"limit": 1}).json()
    assert {"object", "hasMore", "totalCount", "limit", "offset", "data"} <= set(page)
    assert page["object"] == "list"
    assert page["limit"] == 1


def test_payment_shape(api: httpx.Client, customer: dict):
    payment = api.post(
        "/v3/payments",
        json={
            "customer": customer["id"],
            "billingType": "PIX",
            "value": 15.0,
            "dueDate": due(),
        },
    ).json()
    assert payment["object"] == "payment"
    assert payment["id"].startswith("pay_")
    assert payment["status"] == "PENDING"
    assert payment["value"] == 15.0
    assert payment["netValue"] < payment["value"]
    assert payment["deleted"] is False


def test_payment_for_unknown_customer_is_400(api: httpx.Client):
    response = api.post(
        "/v3/payments",
        json={
            "customer": "cus_000000000000",
            "billingType": "PIX",
            "value": 10,
            "dueDate": due(),
        },
    )
    assert response.status_code == 400


def test_pix_qr_code(api: httpx.Client, customer: dict):
    payment = api.post(
        "/v3/payments",
        json={
            "customer": customer["id"],
            "billingType": "PIX",
            "value": 12.34,
            "dueDate": due(),
        },
    ).json()
    qr = api.get(f"/v3/payments/{payment['id']}/pixQrCode").json()
    assert {"encodedImage", "payload", "expirationDate"} <= set(qr)
    assert qr["payload"].startswith("000201")


def test_boleto_identification_field(api: httpx.Client, customer: dict):
    payment = api.post(
        "/v3/payments",
        json={
            "customer": customer["id"],
            "billingType": "BOLETO",
            "value": 20.0,
            "dueDate": due(),
        },
    ).json()
    data = api.get(f"/v3/payments/{payment['id']}/identificationField").json()
    assert len(data["identificationField"]) == 47
    assert len(data["barCode"]) == 44


def test_confirmation_moves_status_and_credits_net_value(api: httpx.Client, customer: dict):
    before = balance(api)
    payment = api.post(
        "/v3/payments",
        json={
            "customer": customer["id"],
            "billingType": "PIX",
            "value": 25.0,
            "dueDate": due(),
        },
    ).json()
    confirmed = api.post(f"/v3/sandbox/payment/{payment['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] in ("RECEIVED", "CONFIRMED")
    assert round(balance(api) - before, 2) == round(confirmed.json()["netValue"], 2)


def test_delete_and_restore(api: httpx.Client, customer: dict):
    payment = api.post(
        "/v3/payments",
        json={
            "customer": customer["id"],
            "billingType": "BOLETO",
            "value": 30.0,
            "dueDate": due(),
        },
    ).json()
    assert api.delete(f"/v3/payments/{payment['id']}").json() == {
        "deleted": True,
        "id": payment["id"],
    }
    restored = api.post(f"/v3/payments/{payment['id']}/restore").json()
    assert restored["deleted"] is False


def test_transfer_larger_than_balance_is_400(api: httpx.Client):
    response = api.post(
        "/v3/transfers",
        json={
            "value": round(balance(api) + 1_000_000, 2),
            "pixAddressKey": "conformance@example.com",
            "pixAddressKeyType": "EMAIL",
        },
    )
    assert response.status_code == 400
    assert response.json()["errors"]
