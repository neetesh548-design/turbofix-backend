import pytest

from tests.conftest import ACME_MAINTENANCE_HEAD, ACME_OWNER, ACME_SUPERVISOR, auth_headers, login


@pytest.mark.parametrize("kind_path,create_body", [
    (
        "spare-parts",
        {"machine_id": "TF-ACME3-M001", "part_name": "Test Bearing", "part_number": "BRG-999",
         "quantity_on_hand": 5, "unit": "pcs", "reorder_level": 1, "supplier": "Test Co", "notes": ""},
    ),
    (
        "consumables",
        {"machine_id": "TF-ACME3-M001", "name": "Test Lubricant", "quantity_on_hand": 10,
         "unit": "litres", "reorder_level": 2, "notes": ""},
    ),
])
class TestSparePartsAndConsumablesShareShape:
    """Both endpoint groups wrap app.parts_store's kind-based functions identically,
    so the same behavior is verified for both without duplicating every test."""

    def test_list_scoped_to_company(self, vault_client, kind_path, create_body):
        token = login(vault_client, *ACME_OWNER)
        resp = vault_client.get(f"/vault/{kind_path}", headers=auth_headers(token))
        assert resp.status_code == 200
        assert all(item["company_code"] == "ACME3" for item in resp.json())

    def test_maintenance_head_can_create(self, vault_client, kind_path, create_body):
        token = login(vault_client, *ACME_MAINTENANCE_HEAD)
        resp = vault_client.post(f"/vault/{kind_path}", json=create_body, headers=auth_headers(token))
        assert resp.status_code == 201, resp.text
        item = resp.json()
        assert item["company_code"] == "ACME3"
        assert item["machine_id"] == create_body["machine_id"]

    def test_supervisor_cannot_create(self, vault_client, kind_path, create_body):
        token = login(vault_client, *ACME_SUPERVISOR)
        resp = vault_client.post(f"/vault/{kind_path}", json=create_body, headers=auth_headers(token))
        assert resp.status_code == 403

    def test_create_rejects_machine_from_another_company(self, vault_client, kind_path, create_body):
        token = login(vault_client, *ACME_MAINTENANCE_HEAD)
        body = {**create_body, "machine_id": "TF-BETA1-M001"}
        resp = vault_client.post(f"/vault/{kind_path}", json=body, headers=auth_headers(token))
        assert resp.status_code == 404

    def test_update_and_delete_roundtrip(self, vault_client, kind_path, create_body):
        maint_token = login(vault_client, *ACME_MAINTENANCE_HEAD)
        created = vault_client.post(f"/vault/{kind_path}", json=create_body, headers=auth_headers(maint_token)).json()
        item_id = created[list(created.keys())[0]]  # first field is always the *_id primary key

        update_resp = vault_client.patch(
            f"/vault/{kind_path}/{item_id}", json={"quantity_on_hand": 42}, headers=auth_headers(maint_token)
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["quantity_on_hand"] == 42

        supervisor_token = login(vault_client, *ACME_SUPERVISOR)
        forbidden = vault_client.delete(f"/vault/{kind_path}/{item_id}", headers=auth_headers(supervisor_token))
        assert forbidden.status_code == 403

        deleted = vault_client.delete(f"/vault/{kind_path}/{item_id}", headers=auth_headers(maint_token))
        assert deleted.status_code == 204

        list_after = vault_client.get(f"/vault/{kind_path}", headers=auth_headers(maint_token)).json()
        assert all(i[list(i.keys())[0]] != item_id for i in list_after)
