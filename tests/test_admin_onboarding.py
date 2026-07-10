import pytest
import openpyxl

from app import config
from app.repositories.base import USERS_HEADER
from tests.conftest import auth_headers, login

ADMIN_PW = "test-admin-pw"


@pytest.fixture(autouse=True)
def _fixed_admin_password(monkeypatch):
    monkeypatch.setattr(config, "PLATFORM_ADMIN_PASSWORD", ADMIN_PW)


def admin_token(client):
    resp = client.post("/admin/login", json={"password": ADMIN_PW})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_onboard_company_success(vault_client):
    at = admin_token(vault_client)
    h = auth_headers(at)
    
    payload = {
        "company_code": "TEST1",
        "company_name": "Test Engineering",
        "admin_contact_phone": "+919900099000",
        "owner_name": "Test Owner",
        "owner_email": "owner@test.com",
        "owner_password": "owner-password-123",
        "machine_quota": 8
    }
    
    resp = vault_client.post("/admin/companies", json=payload, headers=h)
    assert resp.status_code == 201, resp.text
    
    data = resp.json()
    assert data["company_code"] == "TEST1"
    assert "owner_user_id" in data
    
    # Try to log in with the new owner credentials
    token = login(vault_client, "owner@test.com", "owner-password-123")
    assert token is not None


def test_onboard_company_duplicate_rejected(vault_client):
    at = admin_token(vault_client)
    h = auth_headers(at)
    
    payload = {
        "company_code": "ACME3",  # Already exists in seed tracker
        "company_name": "Acme Dupe",
        "admin_contact_phone": "+919900099001",
        "owner_name": "Duplicate Owner",
        "owner_email": "dupe@acme.com",
        "owner_password": "dupe-password-123",
        "machine_quota": 5
    }
    
    resp = vault_client.post("/admin/companies", json=payload, headers=h)
    assert resp.status_code == 409
    assert "company code already exists" in resp.json()["detail"].lower()


def test_onboard_company_invalid_password_rejected(vault_client):
    at = admin_token(vault_client)
    h = auth_headers(at)
    
    payload = {
        "company_code": "TEST2",
        "company_name": "Test Engineering 2",
        "admin_contact_phone": "+919900099002",
        "owner_name": "Short Pass Owner",
        "owner_email": "short@test.com",
        "owner_password": "short",  # Less than 8 characters
        "machine_quota": 5
    }
    
    resp = vault_client.post("/admin/companies", json=payload, headers=h)
    assert resp.status_code == 400
    assert "at least 8 characters" in resp.json()["detail"].lower()


def test_onboard_company_unauthorized_rejected(vault_client):
    payload = {
        "company_code": "TEST3",
        "company_name": "Test Engineering 3",
        "admin_contact_phone": "+919900099003",
        "owner_name": "Unauthorized Owner",
        "owner_email": "unauth@test.com",
        "owner_password": "password-123",
        "machine_quota": 5
    }
    
    # Missing headers
    resp = vault_client.post("/admin/companies", json=payload)
    assert resp.status_code == 401
