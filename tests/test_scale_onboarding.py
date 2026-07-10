import pytest
import io
import random
from app import config
from tests.conftest import auth_headers, login

ADMIN_PW = "test-admin-pw"

@pytest.fixture(autouse=True)
def _fixed_admin_password(monkeypatch):
    monkeypatch.setattr(config, "PLATFORM_ADMIN_PASSWORD", ADMIN_PW)

def admin_token(client):
    resp = client.post("/admin/login", json={"password": ADMIN_PW})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]

def test_scale_onboarding_5_owners_with_supervisors(vault_client):
    at = admin_token(vault_client)
    admin_h = auth_headers(at)

    # We will create 5 business owners (DELTA1 to DELTA5)
    for i in range(1, 6):
        company_code = f"DELTA{i}"
        company_name = f"Delta Industrial Group {i}"
        admin_phone = f"+91990000000{i}"
        owner_email = f"owner{i}@delta.com"
        owner_password = f"ownerpass{i}123"
        owner_name = f"Owner Delta {i}"

        # 1. Register Business Owner
        payment_file = io.BytesIO(f"screenshot content for Delta {i}".encode())
        register_payload = {
            "company_code": company_code,
            "company_name": company_name,
            "admin_contact_phone": admin_phone,
            "owner_name": owner_name,
            "owner_email": owner_email,
            "owner_password": owner_password,
        }
        
        resp = vault_client.post(
            "/auth/register",
            data=register_payload,
            files={"payment_screenshot": (f"screenshot_{i}.jpg", payment_file, "image/jpeg")}
        )
        assert resp.status_code == 201, f"Failed registering owner {i}: {resp.text}"

        # 2. Approve via Admin Console and increase quota to accommodate all machines
        approve_resp = vault_client.post(
            f"/admin/companies/{company_code}",
            json={"approved": True, "machine_quota": 10},
            headers=admin_h
        )
        assert approve_resp.status_code == 200, f"Failed approving company {company_code}"

        # 3. Log in as Business Owner
        owner_token = login(vault_client, owner_email, owner_password)
        assert owner_token is not None
        owner_h = auth_headers(owner_token)

        # 4. Onboard 3-5 supervisors (let's alternate or do a randomized range, e.g., 3, 4, or 5)
        num_supervisors = 3 + (i % 3)  # delta1: 4, delta2: 5, delta3: 3, delta4: 4, delta5: 5
        supervisor_ids = []

        for j in range(1, num_supervisors + 1):
            sup_name = f"Delta {i} Supervisor {j}"
            sup_phone = f"+9199000{i}000{j}"
            sup_email = f"supervisor_{i}_{j}@delta.com"
            sup_password = f"suppass_{i}_{j}_123"

            sup_payload = {
                "name": sup_name,
                "phone": sup_phone,
                "email": sup_email,
                "password": sup_password
            }
            sup_resp = vault_client.post("/auth/supervisors", json=sup_payload, headers=owner_h)
            assert sup_resp.status_code == 201, f"Failed creating supervisor {j} for owner {i}"
            sup_id = sup_resp.json()["user_id"]
            supervisor_ids.append(sup_id)

            # 5. Create a machine and assign to this supervisor
            machine_payload = {
                "machine_name": f"Lathe i{i} j{j}",
                "location": f"Zone {j}",
                "assigned_technician_phone": f"+9199000{i}111{j}",
                "supervisor_id": sup_id
            }
            mach_resp = vault_client.post("/vault/machines", json=machine_payload, headers=owner_h)
            assert mach_resp.status_code == 201
            mach_data = mach_resp.json()
            assert mach_data["supervisor_id"] == sup_id

            # 6. Verify supervisor login & scoped dashboard visibility
            sup_token = login(vault_client, sup_email, sup_password)
            assert sup_token is not None
            sup_h = auth_headers(sup_token)

            dash_resp = vault_client.get("/vault/dashboard", headers=sup_h)
            assert dash_resp.status_code == 200
            dash_data = dash_resp.json()
            # Each supervisor is only assigned exactly 1 machine in this test
            assert dash_data["kpis"]["total_machines"] == 1, f"Supervisor {sup_email} has wrong scoped count"

        # 7. Verify Owner dashboard maps all supervisors and machines correctly
        owner_dash_resp = vault_client.get("/vault/dashboard", headers=owner_h)
        assert owner_dash_resp.status_code == 200
        owner_dash_data = owner_dash_resp.json()
        assert len(owner_dash_data["supervisors"]) == num_supervisors
        for sup_data in owner_dash_data["supervisors"]:
            assert len(sup_data["machines"]) == 1
            assert sup_data["supervisor_id"] in supervisor_ids
