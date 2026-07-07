from tests.conftest import ACME_OWNER, auth_headers, login


def test_login_success_returns_token_and_role(vault_client):
    identifier, password = ACME_OWNER
    resp = vault_client.post("/auth/login", json={"identifier": identifier, "password": password})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "owner"
    assert body["user"]["company_code"] == "ACME3"
    assert body["access_token"]


def test_login_by_phone_also_works(vault_client):
    resp = vault_client.post("/auth/login", json={"identifier": "+919820012345", "password": "AcmeOwner@2026"})
    assert resp.status_code == 200
    assert resp.json()["user"]["role"] == "owner"


def test_login_wrong_password_rejected(vault_client):
    identifier, _ = ACME_OWNER
    resp = vault_client.post("/auth/login", json={"identifier": identifier, "password": "not-the-password"})
    assert resp.status_code == 401


def test_login_unknown_identifier_rejected(vault_client):
    resp = vault_client.post("/auth/login", json={"identifier": "nobody@example.com", "password": "whatever"})
    assert resp.status_code == 401


def test_protected_endpoint_requires_token(vault_client):
    resp = vault_client.get("/vault/documents")
    assert resp.status_code == 401


def test_protected_endpoint_rejects_garbage_token(vault_client):
    resp = vault_client.get("/vault/documents", headers=auth_headers("not-a-real-token"))
    assert resp.status_code == 401


def test_protected_endpoint_accepts_valid_token(vault_client):
    token = login(vault_client, *ACME_OWNER)
    resp = vault_client.get("/vault/documents", headers=auth_headers(token))
    assert resp.status_code == 200
