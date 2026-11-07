from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from saci.app import create_app
from saci.clock import Clock
from saci.config import Settings
from saci.errors import AsaasError
from saci.money import from_cents, to_cents
from saci.validators import cpf_cnpj, parse_date, positive_amount


def test_settings_are_read_from_the_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SACI_API_KEYS", " key-a, key-b ,")
    monkeypatch.setenv("SACI_FEE_PIX_PAYMENT", "0.99")
    monkeypatch.setenv("SACI_TRANSFER_MODE", "manual")
    monkeypatch.setenv("SACI_DELIVERY_MODE", "manual")
    monkeypatch.setenv("SACI_WEBHOOK_MAX_FAILURES", "3")

    settings = Settings.from_env()

    assert settings.api_keys == frozenset({"key-a", "key-b"})
    assert settings.fees.pix_payment == Decimal("0.99")
    assert settings.transfer_mode == "manual"
    assert settings.delivery_mode == "manual"
    assert settings.webhook_max_failures == 3


@pytest.mark.parametrize("variable", ["TRANSFER_MODE", "DELIVERY_MODE"])
def test_invalid_mode_fails_fast(monkeypatch: pytest.MonkeyPatch, variable: str):
    monkeypatch.setenv(f"SACI_{variable}", "sometimes")
    with pytest.raises(ValueError, match=variable):
        Settings.from_env()


def test_api_key_allowlist_rejects_unknown_keys():
    app = create_app(Settings(api_keys=frozenset({"$aact_allowed"}), delivery_mode="manual"))
    with TestClient(app) as client:
        allowed = client.get("/v3/finance/balance", headers={"access_token": "$aact_allowed"})
        denied = client.get("/v3/finance/balance", headers={"access_token": "$aact_other"})
    assert allowed.status_code == 200
    assert denied.status_code == 401


def test_malformed_json_body_is_a_400(client: TestClient):
    response = client.post(
        "/v3/customers", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "invalid_object"


def test_non_object_json_body_is_a_400(client: TestClient):
    assert client.post("/v3/customers", json=[1, 2]).status_code == 400


def test_bad_pagination_parameters_are_a_400(client: TestClient):
    assert client.get("/v3/customers", params={"limit": "ten"}).status_code == 400


@pytest.mark.parametrize(
    "document",
    ["", "123", "000.000.000-00", "529.982.247-24", "11.111.111/1111-11", "11.222.333/0001-80"],
)
def test_invalid_documents(document: str):
    with pytest.raises(AsaasError):
        cpf_cnpj(document)


@pytest.mark.parametrize("value", [None, True, "abc", 0, -5])
def test_invalid_amounts(value: object):
    with pytest.raises(AsaasError):
        positive_amount(value)


def test_amounts_are_rounded_half_up_to_cents():
    # round(10.005, 2) on a float gives 10.0; money must not depend on binary floats.
    assert positive_amount("10.005") == 10.01
    assert positive_amount(10.005) == 10.01
    assert positive_amount(19.9) == 19.9


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e400"])
def test_non_finite_amounts_are_rejected(value: str):
    with pytest.raises(AsaasError):
        positive_amount(value)


@pytest.mark.parametrize("value", [None, 20260301, "01/03/2026", "2026-13-01"])
def test_invalid_dates(value: object):
    with pytest.raises(AsaasError):
        parse_date(value, "dueDate")


def test_money_round_trip_and_rejection():
    assert to_cents("1.005") == 101
    assert from_cents(to_cents(Decimal("123.45"))) == 123.45
    with pytest.raises(ValueError):
        to_cents("not money")


def test_clock_advances_when_not_frozen():
    clock = Clock()
    before = clock.now()
    clock.advance(timedelta(days=2))
    assert clock.now() - before >= timedelta(days=2)
    assert not clock.frozen
    clock.freeze(datetime(2026, 1, 1, 12))
    clock.advance(timedelta(hours=1))
    assert clock.now() == datetime(2026, 1, 1, 13)
    clock.reset()
    assert not clock.frozen


def test_invalid_clock_value_is_a_400(client: TestClient):
    assert client.post("/_saci/clock", json={"now": "yesterday"}).status_code == 400


def test_health_reports_version(client: TestClient):
    body = client.get("/_saci/health").json()
    assert body["status"] == "ok"
    assert body["version"]
