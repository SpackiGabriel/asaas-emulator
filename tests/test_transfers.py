import pytest
from fastapi.testclient import TestClient

from saci.config import Settings

PIX = {"pixAddressKey": "maria@example.com", "pixAddressKeyType": "EMAIL"}


@pytest.fixture
def funded(client: TestClient, make_payment) -> TestClient:
    payment = make_payment(value=101.99)  # nets exactly 100.00
    client.post(f"/v3/sandbox/payment/{payment['id']}/confirm")
    assert client.get("/v3/finance/balance").json() == {"balance": 100.0}
    return client


def test_balance_starts_at_zero(client: TestClient):
    # A generous default balance would hide the most common real-world failure.
    assert client.get("/v3/finance/balance").json() == {"balance": 0.0}


def test_pix_transfer_debits_balance_and_settles(funded: TestClient):
    transfer = funded.post("/v3/transfers", json={"value": 60, **PIX}).json()
    assert transfer["status"] == "DONE"
    assert transfer["type"] == "PIX"
    assert transfer["endToEndIdentifier"].startswith("E")
    assert transfer["bankAccount"] == {"pixAddressKey": "maria@example.com"}
    assert funded.get("/v3/finance/balance").json() == {"balance": 40.0}


def test_insufficient_balance_is_rejected_without_debit(funded: TestClient):
    response = funded.post("/v3/transfers", json={"value": 100.01, **PIX})
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "insufficient_balance"
    assert funded.get("/v3/finance/balance").json() == {"balance": 100.0}


def test_concurrent_transfers_never_overdraw(funded: TestClient):
    """The debit is a single conditional UPDATE, so racing requests cannot both win."""
    from concurrent.futures import ThreadPoolExecutor

    def attempt(_):
        return funded.post("/v3/transfers", json={"value": 30, **PIX}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(attempt, range(8)))

    assert codes.count(200) == 3
    assert codes.count(400) == 5
    assert funded.get("/v3/finance/balance").json() == {"balance": 10.0}


def test_transfer_requires_a_destination(funded: TestClient):
    response = funded.post("/v3/transfers", json={"value": 10})
    assert response.status_code == 400


class TestManualSettlement:
    """Transfers wait for an explicit outcome, like a slow bank."""

    @pytest.fixture
    def settings(self) -> Settings:
        return Settings(delivery_mode="manual", transfer_mode="manual", webhook_retry_seconds=0)

    def test_manual_mode_keeps_transfers_pending_until_settled(self, funded: TestClient):
        transfer = funded.post("/v3/transfers", json={"value": 10, **PIX}).json()
        assert transfer["status"] == "PENDING"
        done = funded.post(f"/_saci/transfers/{transfer['id']}/done").json()
        assert done["status"] == "DONE"

    def test_failed_transfer_returns_the_money(self, funded: TestClient):
        transfer = funded.post("/v3/transfers", json={"value": 10, **PIX}).json()
        failed = funded.post(
            f"/_saci/transfers/{transfer['id']}/fail", json={"failReason": "Chave inexistente"}
        ).json()
        assert (failed["status"], failed["failReason"]) == ("FAILED", "Chave inexistente")
        assert funded.get("/v3/finance/balance").json() == {"balance": 100.0}

    def test_cancel_pending_transfer(self, funded: TestClient):
        transfer = funded.post("/v3/transfers", json={"value": 10, **PIX}).json()
        cancelled = funded.delete(f"/v3/transfers/{transfer['id']}/cancel").json()
        assert cancelled["status"] == "CANCELLED"
        assert funded.get("/v3/finance/balance").json() == {"balance": 100.0}
