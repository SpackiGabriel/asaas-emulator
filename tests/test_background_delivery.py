"""The default mode: a worker thread delivers webhooks without anyone asking."""

import time

import pytest
from fastapi.testclient import TestClient

from asaas_emulator.config import Settings

from .conftest import Receiver


@pytest.fixture
def settings() -> Settings:
    return Settings(delivery_mode="background", webhook_retry_seconds=0.05)


def wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def test_worker_delivers_and_retries_on_its_own(
    client: TestClient, receiver: Receiver, make_payment
):
    client.post(
        "/v3/webhooks",
        json={
            "name": "bg",
            "url": receiver.url,
            "email": "dev@example.com",
            "enabled": True,
            "interrupted": False,
            "events": ["PAYMENT_CREATED", "PAYMENT_RECEIVED"],
            "sendType": "SEQUENTIALLY",
        },
    )
    receiver.status_codes = [503]
    payment = make_payment()
    client.post(f"/v3/sandbox/payment/{payment['id']}/confirm")

    wait_for(lambda: receiver.events.count("PAYMENT_RECEIVED") == 1)
    assert receiver.events == ["PAYMENT_CREATED", "PAYMENT_CREATED", "PAYMENT_RECEIVED"]
