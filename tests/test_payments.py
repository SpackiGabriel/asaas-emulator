from fastapi.testclient import TestClient

from saci.codes import _crc16_ccitt, _mod10, _mod11_barcode

from .conftest import TODAY


def test_new_payment_is_pending_with_fee_deducted_from_net_value(make_payment):
    payment = make_payment("PIX", value=150.0)
    assert payment["id"] == "pay_000000000001"
    assert payment["status"] == "PENDING"
    assert payment["value"] == 150.0
    assert payment["netValue"] == 148.01  # default flat Pix fee of 1.99


def test_payment_requires_existing_customer(client: TestClient):
    response = client.post(
        "/v3/payments",
        json={"customer": "cus_nope", "billingType": "PIX", "value": 10, "dueDate": "2026-03-10"},
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "invalid_customer"


def test_due_date_in_the_past_is_rejected(make_payment, client: TestClient, customer: dict):
    response = client.post(
        "/v3/payments",
        json={
            "customer": customer["id"],
            "billingType": "PIX",
            "value": 10,
            "dueDate": "2026-03-01",
        },
    )
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "invalid_dueDate"


def test_sandbox_confirmation_credits_net_value_to_balance(client: TestClient, make_payment):
    payment = make_payment("BOLETO", value=200.0)
    assert client.get("/v3/finance/balance").json() == {"balance": 0.0}

    confirmed = client.post(f"/v3/sandbox/payment/{payment['id']}/confirm").json()

    assert confirmed["status"] == "RECEIVED"
    assert confirmed["paymentDate"] == TODAY.isoformat()
    assert client.get("/v3/finance/balance").json() == {"balance": 198.01}


def test_payment_cannot_be_confirmed_twice(client: TestClient, make_payment):
    payment = make_payment()
    client.post(f"/v3/sandbox/payment/{payment['id']}/confirm")
    again = client.post(f"/v3/sandbox/payment/{payment['id']}/confirm")
    assert again.status_code == 400
    assert client.get("/v3/finance/balance").json() == {"balance": 98.01}


def test_moving_the_clock_makes_pending_payments_overdue(client: TestClient, make_payment):
    payment = make_payment(days=1)
    client.post("/_saci/clock", json={"advanceDays": 2})
    assert client.get(f"/v3/payments/{payment['id']}/status").json() == {"status": "OVERDUE"}


def test_forced_overdue_then_confirmation_still_works(client: TestClient, make_payment):
    payment = make_payment()
    sandbox = f"/v3/sandbox/payment/{payment['id']}"
    assert client.post(f"{sandbox}/overdue").json()["status"] == "OVERDUE"
    assert client.post(f"{sandbox}/confirm").json()["status"] == "RECEIVED"


def test_update_delete_and_restore(client: TestClient, make_payment):
    payment = make_payment(value=100)
    updated = client.put(f"/v3/payments/{payment['id']}", json={"value": 120}).json()
    assert (updated["value"], updated["netValue"]) == (120.0, 118.01)

    assert client.delete(f"/v3/payments/{payment['id']}").json()["deleted"] is True
    assert client.get("/v3/payments").json()["totalCount"] == 0
    assert client.get(f"/v3/payments/{payment['id']}").json()["deleted"] is True

    client.post(f"/v3/payments/{payment['id']}/restore")
    assert client.get("/v3/payments").json()["totalCount"] == 1


def test_received_payment_cannot_be_deleted(client: TestClient, make_payment):
    payment = make_payment()
    client.post(f"/v3/sandbox/payment/{payment['id']}/confirm")
    assert client.delete(f"/v3/payments/{payment['id']}").status_code == 400


def test_cash_receipt_does_not_touch_balance_and_can_be_undone(client, make_payment):
    payment = make_payment()
    received = client.post(f"/v3/payments/{payment['id']}/receiveInCash", json={}).json()
    assert received["status"] == "RECEIVED_IN_CASH"
    assert client.get("/v3/finance/balance").json() == {"balance": 0.0}
    undone = client.post(f"/v3/payments/{payment['id']}/undoReceivedInCash").json()
    assert undone["status"] == "PENDING"


def test_list_filters(client: TestClient, make_payment):
    make_payment("PIX", days=1)
    make_payment("BOLETO", days=10)
    assert client.get("/v3/payments", params={"billingType": "BOLETO"}).json()["totalCount"] == 1
    late = client.get("/v3/payments", params={"dueDate[ge]": "2026-03-05"}).json()
    assert [p["billingType"] for p in late["data"]] == ["BOLETO"]


def test_pix_qr_code_payload_is_a_valid_br_code(client: TestClient, make_payment):
    payment = make_payment("PIX", value=42.5)
    qr = client.get(f"/v3/payments/{payment['id']}/pixQrCode").json()
    payload = qr["payload"]
    assert payload.startswith("000201")
    assert "5405" + "42.50" in payload
    assert payload[-4:] == _crc16_ccitt(payload[:-4])
    assert qr["encodedImage"]  # base64 PNG


def test_boleto_line_and_barcode_have_valid_check_digits(client: TestClient, make_payment):
    payment = make_payment("BOLETO", value=10.5)
    data = client.get(f"/v3/payments/{payment['id']}/identificationField").json()
    line, barcode = data["identificationField"], data["barCode"]
    assert (len(line), len(barcode)) == (47, 44)
    assert barcode.startswith("461")
    assert barcode[4] == str(_mod11_barcode(barcode[:4] + barcode[5:]))
    for start, end in ((0, 9), (10, 20), (21, 31)):
        assert line[end] == str(_mod10(line[start:end]))
    assert barcode.endswith("0000001050") is False  # value sits before the free field
    assert barcode[9:19] == "0000001050"


def test_identification_field_only_for_boletos(client: TestClient, make_payment):
    payment = make_payment("PIX")
    assert client.get(f"/v3/payments/{payment['id']}/identificationField").status_code == 400
