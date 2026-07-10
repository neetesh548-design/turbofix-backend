import pytest
import io
from app import config, email_client
from tests.conftest import auth_headers, login

ADMIN_PW = "test-admin-pw"

@pytest.fixture(autouse=True)
def _fixed_admin_password(monkeypatch):
    monkeypatch.setattr(config, "PLATFORM_ADMIN_PASSWORD", ADMIN_PW)

def admin_token(client):
    resp = client.post("/admin/login", json={"password": ADMIN_PW})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]

def test_full_owner_and_supervisor_flow(vault_client, monkeypatch):
    # Track emails sent
    sent_emails = []
    def mock_send_email(to, subject, body):
        sent_emails.append({"to": to, "subject": subject, "body": body})
    monkeypatch.setattr(email_client, "send_email", mock_send_email)

    # 1. Register a new company with payment screenshot (Multipart Form Data)
    payment_file = io.BytesIO(b"dummy screenshot content")
    
    register_payload = {
        "company_code": "DELTA",
        "company_name": "Delta Industries",
        "admin_contact_phone": "+918800088000",
        "owner_name": "Delta Owner",
        "owner_email": "owner@delta.com",
        "owner_password": "ownerpassword123",
    }
    
    resp = vault_client.post(
        "/auth/register",
        data=register_payload,
        files={"payment_screenshot": ("screenshot.jpg", payment_file, "image/jpeg")}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "pending_approval"

    # 2. Try logging in with the owner credentials (should fail because not approved yet)
    login_resp = vault_client.post(
        "/auth/login",
        json={"identifier": "owner@delta.com", "password": "ownerpassword123"}
    )
    assert login_resp.status_code == 403, login_resp.text
    assert "pending approval" in login_resp.json()["detail"].lower()

    # 3. Admin logs in, sees the company list with has_payment_screenshot = True
    at = admin_token(vault_client)
    admin_h = auth_headers(at)
    
    companies_resp = vault_client.get("/admin/companies", headers=admin_h)
    assert companies_resp.status_code == 200
    delta_co = next((c for c in companies_resp.json() if c["company_code"] == "DELTA"), None)
    assert delta_co is not None
    assert delta_co["approved"] is False
    assert delta_co["has_payment_screenshot"] is True

    # 4. Admin downloads / views the payment screenshot
    screenshot_resp = vault_client.get("/admin/companies/DELTA/payment-screenshot", headers=admin_h)
    assert screenshot_resp.status_code == 200
    assert screenshot_resp.content == b"dummy screenshot content"
    assert "image/jpeg" in screenshot_resp.headers["content-type"]

    # 5. Admin approves the company -> triggers automated welcome email
    approve_resp = vault_client.post(
        "/admin/companies/DELTA",
        json={"approved": True},
        headers=admin_h
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["approved"] is True

    # Verify welcome email was dispatched
    assert len(sent_emails) == 1
    assert sent_emails[0]["to"] == "owner@delta.com"
    assert "Approved" in sent_emails[0]["subject"]

    # 6. Now Owner logs in successfully
    owner_token = login(vault_client, "owner@delta.com", "ownerpassword123")
    assert owner_token is not None
    owner_h = auth_headers(owner_token)

    # 7. Owner onboards a supervisor
    sup_payload = {
        "name": "Delta Supervisor",
        "phone": "+918800099000",
        "email": "supervisor@delta.com",
        "password": "supervisorpassword123"
    }
    sup_resp = vault_client.post("/auth/supervisors", json=sup_payload, headers=owner_h)
    assert sup_resp.status_code == 201, sup_resp.text
    assert sup_resp.json()["status"] == "created"
    supervisor_id = sup_resp.json()["user_id"]
    assert supervisor_id is not None

    # Verify supervisor list endpoint for owners
    sups_list_resp = vault_client.get("/vault/supervisors", headers=owner_h)
    assert sups_list_resp.status_code == 200
    assert any(s["user_id"] == supervisor_id for s in sups_list_resp.json())

    # 8. Owner adds a machine and assigns it to the supervisor
    machine_payload = {
        "machine_name": "Delta Lathe 1",
        "location": "Aisle 3",
        "assigned_technician_phone": "+919900011000",
        "supervisor_id": supervisor_id
    }
    mach_resp = vault_client.post("/vault/machines", json=machine_payload, headers=owner_h)
    assert mach_resp.status_code == 201
    machine_id = mach_resp.json()["machine_id"]
    assert mach_resp.json()["supervisor_id"] == supervisor_id

    # 9. Supervisor logs in and accesses dashboard
    sup_token = login(vault_client, "supervisor@delta.com", "supervisorpassword123")
    assert sup_token is not None
    sup_h = auth_headers(sup_token)

    # Supervisor dashboard should be scoped (total machines = 1)
    dash_resp = vault_client.get("/vault/dashboard", headers=sup_h)
    assert dash_resp.status_code == 200
    dash_data = dash_resp.json()
    assert dash_data["kpis"]["total_machines"] == 1

    # 10. Owner views dashboard, checks supervisors and unassigned machines
    owner_dash_resp = vault_client.get("/vault/dashboard", headers=owner_h)
    assert owner_dash_resp.status_code == 200
    owner_dash_data = owner_dash_resp.json()
    assert "supervisors" in owner_dash_data
    delta_sups = owner_dash_data["supervisors"]
    assert len(delta_sups) == 1
    assert delta_sups[0]["supervisor_id"] == supervisor_id
    assert delta_sups[0]["machines"][0]["machine_id"] == machine_id

    # 11. Edit supervisor name
    edit_resp = vault_client.put(
        f"/auth/supervisors/{supervisor_id}",
        json={"name": "Delta Supervisor Modified"},
        headers=owner_h
    )
    assert edit_resp.status_code == 200
    assert edit_resp.json()["status"] == "updated"

    # Verify update reflected in supervisors list
    sups_list_resp = vault_client.get("/vault/supervisors", headers=owner_h)
    assert sups_list_resp.status_code == 200
    updated_sup = next((s for s in sups_list_resp.json() if s["user_id"] == supervisor_id), None)
    assert updated_sup is not None
    assert updated_sup["name"] == "Delta Supervisor Modified"

    # 11.5 Edit machine supervisor to empty
    edit_mach_resp = vault_client.put(
        f"/vault/machines/{machine_id}",
        json={"supervisor_id": ""},
        headers=owner_h
    )
    assert edit_mach_resp.status_code == 200
    assert edit_mach_resp.json()["status"] == "updated"

    # Verify machine is now unassigned
    owner_dash_resp_mid = vault_client.get("/vault/dashboard", headers=owner_h)
    assert owner_dash_resp_mid.status_code == 200
    assert len(owner_dash_resp_mid.json()["supervisors"][0]["machines"]) == 0
    assert len(owner_dash_resp_mid.json()["unassigned_machines"]) == 1

    # Assign it back so delete supervisor unassignment cascade can still be tested
    edit_mach_resp2 = vault_client.put(
        f"/vault/machines/{machine_id}",
        json={"supervisor_id": supervisor_id},
        headers=owner_h
    )
    assert edit_mach_resp2.status_code == 200

    # 12. Delete supervisor
    del_resp = vault_client.delete(f"/auth/supervisors/{supervisor_id}", headers=owner_h)
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # Verify supervisor is deleted from list
    sups_list_resp2 = vault_client.get("/vault/supervisors", headers=owner_h)
    assert not any(s["user_id"] == supervisor_id for s in sups_list_resp2.json())

    # Verify machine is unassigned (supervisor_id is empty) on owner dashboard
    owner_dash_resp2 = vault_client.get("/vault/dashboard", headers=owner_h)
    assert owner_dash_resp2.status_code == 200
    owner_dash_data2 = owner_dash_resp2.json()
    assert len(owner_dash_data2["supervisors"]) == 0
    assert len(owner_dash_data2["unassigned_machines"]) == 1
    assert owner_dash_data2["unassigned_machines"][0]["machine_id"] == machine_id
