from tests.conftest import ACME_OWNER, auth_headers

# Matches ACME3's admin_contact_phone in the sample tracker data (build_tracker.py) -
# same value as ACME_OWNER's own phone, since Rakesh Shah is the company's admin
# contact.
ACME3_ADMIN_PHONE = "+919820012345"

SIGNUP_BODY = {
    "company_code": "ACME3",
    "admin_contact_phone": ACME3_ADMIN_PHONE,
    "name": "New Supervisor",
    "phone": "+919900055501",
    "email": "new.supervisor@acmeforge.example",
    "password": "correct-horse",
}


def test_signup_success_returns_usable_token(vault_client):
    resp = vault_client.post("/auth/signup", json=SIGNUP_BODY)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "supervisor"
    assert body["user"]["company_code"] == "ACME3"

    # the returned token must work immediately against a protected endpoint
    docs = vault_client.get("/vault/documents", headers=auth_headers(body["access_token"]))
    assert docs.status_code == 200


def test_signup_wrong_admin_phone_rejected(vault_client):
    body = {**SIGNUP_BODY, "admin_contact_phone": "+910000000000"}
    resp = vault_client.post("/auth/signup", json=body)
    assert resp.status_code == 401


def test_signup_unknown_company_rejected_with_same_message_as_wrong_phone(vault_client):
    unknown_company = vault_client.post("/auth/signup", json={**SIGNUP_BODY, "company_code": "NOPE9"})
    wrong_phone = vault_client.post(
        "/auth/signup", json={**SIGNUP_BODY, "phone": "+919900055502", "email": "x2@example.com", "admin_contact_phone": "+910000000000"}
    )
    assert unknown_company.status_code == 401
    assert wrong_phone.status_code == 401
    assert unknown_company.json()["detail"] == wrong_phone.json()["detail"]


def test_signup_duplicate_phone_rejected(vault_client):
    identifier, _ = ACME_OWNER
    body = {**SIGNUP_BODY, "phone": "+919820012345", "email": "someone-else@example.com"}
    resp = vault_client.post("/auth/signup", json=body)
    assert resp.status_code == 409


def test_signup_duplicate_email_rejected(vault_client):
    identifier, _ = ACME_OWNER
    body = {**SIGNUP_BODY, "phone": "+919900055503", "email": identifier}
    resp = vault_client.post("/auth/signup", json=body)
    assert resp.status_code == 409


def test_signup_role_field_ignored_always_supervisor(vault_client):
    body = {**SIGNUP_BODY, "role": "owner"}
    resp = vault_client.post("/auth/signup", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["user"]["role"] == "supervisor"


def test_signup_requires_phone_or_email(vault_client):
    body = {**SIGNUP_BODY, "phone": "", "email": ""}
    resp = vault_client.post("/auth/signup", json=body)
    assert resp.status_code == 400


def test_signup_rejects_short_password(vault_client):
    body = {**SIGNUP_BODY, "password": "short"}
    resp = vault_client.post("/auth/signup", json=body)
    assert resp.status_code == 400
