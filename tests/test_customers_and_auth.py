from fastapi.testclient import TestClient

from .conftest import CNPJ, CPF


def test_requests_without_api_key_are_rejected_like_asaas(client: TestClient):
    response = client.get("/v3/customers", headers={"access_token": ""})
    assert response.status_code == 401
    assert response.json() == {
        "errors": [
            {"code": "invalid_access_token", "description": "A chave de API fornecida é inválida"}
        ]
    }


def test_customer_gets_person_type_from_document(client: TestClient):
    person = client.post("/v3/customers", json={"name": "Ana", "cpfCnpj": CPF}).json()
    company = client.post("/v3/customers", json={"name": "Acme", "cpfCnpj": CNPJ}).json()
    assert (person["personType"], person["cpfCnpj"]) == ("FISICA", "52998224725")
    assert (company["personType"], company["cpfCnpj"]) == ("JURIDICA", "11222333000181")
    assert person["id"] == "cus_000000000001"
    assert company["id"] == "cus_000000000002"


def test_invalid_document_is_rejected(client: TestClient):
    response = client.post("/v3/customers", json={"name": "Ana", "cpfCnpj": "123.456.789-00"})
    assert response.status_code == 400
    assert response.json()["errors"][0]["code"] == "invalid_cpfCnpj"


def test_list_is_paginated_newest_first(client: TestClient):
    for i in range(3):
        client.post("/v3/customers", json={"name": f"C{i}", "cpfCnpj": CPF})
    page = client.get("/v3/customers", params={"limit": 2}).json()
    assert page["object"] == "list"
    assert (page["totalCount"], page["hasMore"], page["limit"]) == (3, True, 2)
    assert [c["name"] for c in page["data"]] == ["C2", "C1"]


def test_delete_hides_customer_from_list_and_restore_brings_it_back(
    client: TestClient, customer: dict
):
    assert client.delete(f"/v3/customers/{customer['id']}").json() == {
        "deleted": True,
        "id": customer["id"],
    }
    assert client.get("/v3/customers").json()["totalCount"] == 0
    restored = client.post(f"/v3/customers/{customer['id']}/restore").json()
    assert restored["deleted"] is False


def test_each_api_key_is_an_isolated_account(client: TestClient, customer: dict):
    other = {"access_token": "$aact_other_tenant"}
    assert client.get("/v3/customers", headers=other).json()["totalCount"] == 0
    assert client.get(f"/v3/customers/{customer['id']}", headers=other).status_code == 404


def test_unknown_id_is_a_bare_404(client: TestClient):
    response = client.get("/v3/customers/cus_999")
    assert response.status_code == 404
    assert response.content == b""
