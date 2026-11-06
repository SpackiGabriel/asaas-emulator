from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from asaas_emulator.app import create_app
from asaas_emulator.config import Settings

# Valid check digits, generated for tests only.
CPF = "529.982.247-25"
CNPJ = "11.222.333/0001-81"
TODAY = date(2026, 3, 2)


@pytest.fixture
def settings() -> Settings:
    return Settings(delivery_mode="manual", webhook_retry_seconds=0)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), headers={"access_token": "$aact_test_key"}) as c:
        c.post("/_emulator/clock", json={"now": f"{TODAY.isoformat()}T09:00:00"})
        yield c


@pytest.fixture
def customer(client: TestClient) -> dict:
    response = client.post("/v3/customers", json={"name": "Maria Souza", "cpfCnpj": CPF})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def make_payment(client: TestClient, customer: dict):
    def _make(billing_type: str = "PIX", value: float = 100.0, days: int = 5, **extra) -> dict:
        body = {
            "customer": customer["id"],
            "billingType": billing_type,
            "value": value,
            "dueDate": (TODAY + timedelta(days=days)).isoformat(),
            **extra,
        }
        response = client.post("/v3/payments", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    return _make


class Receiver:
    """A real HTTP server standing in for an integration's webhook endpoint."""

    def __init__(self) -> None:
        self.requests: list[tuple[dict, dict]] = []
        self.status_codes: list[int] = []  # scripted responses, then 200 forever
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
                receiver.requests.append((dict(self.headers), body))
                status = receiver.status_codes.pop(0) if receiver.status_codes else 200
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/webhook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def events(self) -> list[str]:
        return [body["event"] for _, body in self.requests]

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    r = Receiver()
    yield r
    r.close()
