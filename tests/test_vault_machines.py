from app import config
from tests.conftest import ACME_MAINTENANCE_HEAD, ACME_OWNER, ACME_SUPERVISOR, auth_headers, login

NEW_MACHINE_BODY = {
    "machine_name": "Test Grinder",
    "location": "Shop Floor C",
    "assigned_technician_phone": "+919812340099",
    "informed_phone_1": "+919812340010",
}


def test_owner_can_create_machine(vault_client):
    token = login(vault_client, *ACME_OWNER)
    resp = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token))
    assert resp.status_code == 201, resp.text
    machine = resp.json()
    assert machine["company_code"] == "ACME3"
    assert machine["machine_name"] == "Test Grinder"
    assert machine["machine_id"].startswith("TF-ACME3-M")


def test_supervisor_cannot_create_machine(vault_client):
    token = login(vault_client, *ACME_SUPERVISOR)
    resp = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token))
    assert resp.status_code == 403


def test_machine_ids_increment_monotonically(vault_client):
    token = login(vault_client, *ACME_MAINTENANCE_HEAD)
    first = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token)).json()
    second = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token)).json()

    first_n = int(first["machine_id"].rsplit("M", 1)[1])
    second_n = int(second["machine_id"].rsplit("M", 1)[1])
    assert second_n == first_n + 1


def test_new_machine_gets_a_fresh_id_not_a_reused_one(vault_client):
    token = login(vault_client, *ACME_MAINTENANCE_HEAD)
    existing_ids = {
        m["machine_id"]
        for m in vault_client.get("/vault/machines", headers=auth_headers(token)).json()
    }
    created = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token)).json()
    assert created["machine_id"] not in existing_ids


def test_wa_link_present_when_display_number_configured(vault_client, monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_DISPLAY_NUMBER", "919900012345")
    token = login(vault_client, *ACME_OWNER)
    resp = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token))
    machine = resp.json()
    assert machine["wa_link"].startswith("https://wa.me/919900012345?text=")
    assert machine["machine_id"] in machine["wa_link"]


def test_wa_link_absent_when_display_number_not_configured(vault_client, monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_DISPLAY_NUMBER", "")
    token = login(vault_client, *ACME_OWNER)
    resp = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token))
    assert resp.json()["wa_link"] is None


def test_new_machine_immediately_visible_via_list(vault_client):
    """Proves the machines cache is invalidated on create, not just that the row
    exists on disk - a stale cache would hide the new machine for up to
    MACHINES_CACHE_TTL_SECONDS."""
    token = login(vault_client, *ACME_MAINTENANCE_HEAD)
    # Prime the cache with a read before creating the new machine.
    vault_client.get("/vault/machines", headers=auth_headers(token))

    created = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token)).json()

    listed = vault_client.get("/vault/machines", headers=auth_headers(token)).json()
    assert any(m["machine_id"] == created["machine_id"] for m in listed)


def test_new_machine_resolvable_by_store_get_machine(vault_client):
    """A newly created machine must be resolvable the same way the WhatsApp webhook
    resolves machines (app.store.get_machine), not just via the vault list endpoint."""
    from app import store

    token = login(vault_client, *ACME_MAINTENANCE_HEAD)
    store.load_machines()  # prime the cache before creating

    created = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token)).json()

    machine = store.get_machine(created["machine_id"])
    assert machine is not None
    assert machine["company_code"] == "ACME3"


def test_created_machine_scoped_to_callers_company(vault_client):
    token = login(vault_client, *ACME_OWNER)
    created = vault_client.post("/vault/machines", json=NEW_MACHINE_BODY, headers=auth_headers(token)).json()
    assert created["machine_id"].split("-")[1] == "ACME3"
