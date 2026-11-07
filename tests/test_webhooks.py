from fastapi.testclient import TestClient

from .conftest import Receiver

ALL_PAYMENT_EVENTS = ["PAYMENT_CREATED", "PAYMENT_RECEIVED", "PAYMENT_OVERDUE"]


def register(client: TestClient, receiver: Receiver, **overrides) -> dict:
    body = {
        "name": "integration",
        "url": receiver.url,
        "email": "dev@example.com",
        "enabled": True,
        "interrupted": False,
        "authToken": "a-long-random-token-used-only-in-tests",
        "events": ALL_PAYMENT_EVENTS,
        "sendType": "SEQUENTIALLY",
        **overrides,
    }
    response = client.post("/v3/webhooks", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def deliver(client: TestClient) -> int:
    return client.post("/_saci/webhooks/deliver").json()["attempts"]


def test_webhook_never_echoes_its_token(client: TestClient, receiver: Receiver):
    webhook = register(client, receiver)
    assert webhook["hasAuthToken"] is True
    assert "authToken" not in webhook
    assert "authToken" not in client.get(f"/v3/webhooks/{webhook['id']}").json()


def test_delivery_carries_token_header_and_documented_payload(
    client: TestClient, receiver: Receiver, make_payment
):
    register(client, receiver)
    payment = make_payment()
    client.post(f"/v3/sandbox/payment/{payment['id']}/confirm")

    assert deliver(client) == 2
    assert receiver.events == ["PAYMENT_CREATED", "PAYMENT_RECEIVED"]
    headers, body = receiver.requests[1]
    assert headers["asaas-access-token"] == "a-long-random-token-used-only-in-tests"
    assert set(body) == {"id", "event", "dateCreated", "payment"}
    assert body["id"].startswith("evt_")
    assert body["payment"]["id"] == payment["id"]
    assert body["payment"]["status"] == "RECEIVED"


def test_only_subscribed_events_are_delivered(client, receiver, make_payment):
    register(client, receiver, events=["PAYMENT_RECEIVED"])
    payment = make_payment()
    client.post(f"/v3/sandbox/payment/{payment['id']}/confirm")
    deliver(client)
    assert receiver.events == ["PAYMENT_RECEIVED"]


def test_failed_delivery_is_retried_and_event_id_is_stable(client, receiver, make_payment):
    register(client, receiver)
    receiver.status_codes = [500]
    make_payment()

    deliver(client)  # fails
    deliver(client)  # retried, succeeds

    assert receiver.events == ["PAYMENT_CREATED", "PAYMENT_CREATED"]
    first, second = (body["id"] for _, body in receiver.requests)
    assert first == second  # at-least-once: receivers deduplicate on this id


def test_sequential_webhook_does_not_skip_ahead_of_a_failing_event(client, receiver, make_payment):
    register(client, receiver, sendType="SEQUENTIALLY")
    receiver.status_codes = [500]
    payment = make_payment()
    client.post(f"/v3/sandbox/payment/{payment['id']}/confirm")

    deliver(client)
    assert receiver.events == ["PAYMENT_CREATED"]  # RECEIVED waits behind the failure

    deliver(client)
    deliver(client)
    assert receiver.events == ["PAYMENT_CREATED", "PAYMENT_CREATED", "PAYMENT_RECEIVED"]


def test_non_sequential_webhook_delivers_independently(client, receiver, make_payment):
    register(client, receiver, sendType="NON_SEQUENTIALLY")
    receiver.status_codes = [500]
    payment = make_payment()
    client.post(f"/v3/sandbox/payment/{payment['id']}/confirm")

    deliver(client)
    assert receiver.events == ["PAYMENT_CREATED", "PAYMENT_RECEIVED"]


def test_queue_is_interrupted_after_consecutive_failures(client, receiver, make_payment):
    webhook = register(client, receiver)
    receiver.status_codes = [500] * 15
    make_payment()

    for _ in range(20):
        deliver(client)

    state = client.get(f"/v3/webhooks/{webhook['id']}").json()
    assert state["interrupted"] is True
    assert state["penalizedRequestsCount"] == 15
    assert len(receiver.requests) == 15  # nothing else is attempted while interrupted

    client.put(f"/v3/webhooks/{webhook['id']}", json={"interrupted": False})
    deliver(client)
    assert len(receiver.requests) == 16
    assert receiver.events[-1] == "PAYMENT_CREATED"


def test_unreachable_endpoint_counts_as_failure(client: TestClient, make_payment):
    webhook = client.post(
        "/v3/webhooks",
        json={
            "name": "down",
            "url": "http://127.0.0.1:9/nothing",
            "email": "dev@example.com",
            "enabled": True,
            "interrupted": False,
            "events": ["PAYMENT_CREATED"],
            "sendType": "SEQUENTIALLY",
        },
    ).json()
    make_payment()
    deliver(client)
    assert client.get(f"/v3/webhooks/{webhook['id']}").json()["penalizedRequestsCount"] == 1


def test_events_are_inspectable_without_a_receiver(client: TestClient, make_payment):
    payment = make_payment(days=1)
    client.post("/_saci/clock", json={"advanceDays": 2})
    events = client.get("/_saci/events").json()["data"]
    assert [e["event"] for e in events] == ["PAYMENT_CREATED", "PAYMENT_OVERDUE"]
    assert events[1]["payment"]["id"] == payment["id"]
