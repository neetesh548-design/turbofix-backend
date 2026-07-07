from tests.conftest import (
    ACME_MAINTENANCE_HEAD,
    ACME_OWNER,
    ACME_SUPERVISOR,
    BETA_OWNER,
    auth_headers,
    login,
)


def test_list_documents_scoped_to_own_company(vault_client):
    token = login(vault_client, *ACME_OWNER)
    resp = vault_client.get("/vault/documents", headers=auth_headers(token))
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) > 0
    assert all(d["company_code"] == "ACME3" for d in docs)


def test_list_documents_filtered_by_machine(vault_client):
    token = login(vault_client, *ACME_OWNER)
    resp = vault_client.get("/vault/documents", params={"machine_id": "TF-ACME3-M001"}, headers=auth_headers(token))
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) > 0
    assert all(d["machine_id"] == "TF-ACME3-M001" for d in docs)


def test_maintenance_head_can_upload_document(vault_client):
    token = login(vault_client, *ACME_MAINTENANCE_HEAD)
    resp = vault_client.post(
        "/vault/documents",
        headers=auth_headers(token),
        data={"machine_id": "TF-ACME3-M001", "category": "manual", "title": "Test Manual"},
        files={"file": ("test-manual.pdf", b"%PDF-1.4 fake pdf bytes", "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    doc = resp.json()
    assert doc["category"] == "manual"
    assert doc["company_code"] == "ACME3"

    # and it shows up in the list afterward
    list_resp = vault_client.get("/vault/documents", headers=auth_headers(token))
    assert any(d["document_id"] == doc["document_id"] for d in list_resp.json())


def test_supervisor_cannot_upload_document(vault_client):
    token = login(vault_client, *ACME_SUPERVISOR)
    resp = vault_client.post(
        "/vault/documents",
        headers=auth_headers(token),
        data={"machine_id": "TF-ACME3-M001", "category": "manual", "title": "Test Manual"},
        files={"file": ("test-manual.pdf", b"fake bytes", "application/pdf")},
    )
    assert resp.status_code == 403


def test_upload_rejects_disallowed_file_type(vault_client):
    token = login(vault_client, *ACME_MAINTENANCE_HEAD)
    resp = vault_client.post(
        "/vault/documents",
        headers=auth_headers(token),
        data={"machine_id": "TF-ACME3-M001", "category": "manual", "title": "Suspicious"},
        files={"file": ("payload.exe", b"MZ...", "application/octet-stream")},
    )
    assert resp.status_code == 400


def test_upload_rejects_machine_from_another_company(vault_client):
    token = login(vault_client, *ACME_MAINTENANCE_HEAD)
    resp = vault_client.post(
        "/vault/documents",
        headers=auth_headers(token),
        data={"machine_id": "TF-BETA1-M001", "category": "manual", "title": "Wrong Company"},
        files={"file": ("m.pdf", b"bytes", "application/pdf")},
    )
    assert resp.status_code == 404


def test_upload_then_download_roundtrips_content(vault_client):
    token = login(vault_client, *ACME_MAINTENANCE_HEAD)
    content = b"the real manual content"
    upload = vault_client.post(
        "/vault/documents",
        headers=auth_headers(token),
        data={"machine_id": "TF-ACME3-M001", "category": "manual", "title": "Roundtrip Manual"},
        files={"file": ("roundtrip.pdf", content, "application/pdf")},
    )
    document_id = upload.json()["document_id"]

    download = vault_client.get(f"/vault/documents/{document_id}/download", headers=auth_headers(token))
    assert download.status_code == 200
    assert download.content == content


def test_cannot_download_another_companys_document(vault_client):
    acme_token = login(vault_client, *ACME_OWNER)
    docs = vault_client.get("/vault/documents", headers=auth_headers(acme_token)).json()
    acme_doc_id = docs[0]["document_id"]

    beta_token = login(vault_client, *BETA_OWNER)
    resp = vault_client.get(f"/vault/documents/{acme_doc_id}/download", headers=auth_headers(beta_token))
    assert resp.status_code == 404


def test_owner_can_delete_document(vault_client):
    owner_token = login(vault_client, *ACME_OWNER)
    docs = vault_client.get("/vault/documents", headers=auth_headers(owner_token)).json()
    doc_id = docs[0]["document_id"]

    resp = vault_client.delete(f"/vault/documents/{doc_id}", headers=auth_headers(owner_token))
    assert resp.status_code == 204

    after = vault_client.get("/vault/documents", headers=auth_headers(owner_token)).json()
    assert all(d["document_id"] != doc_id for d in after)


def test_supervisor_cannot_delete_document(vault_client):
    owner_token = login(vault_client, *ACME_OWNER)
    docs = vault_client.get("/vault/documents", headers=auth_headers(owner_token)).json()
    doc_id = docs[0]["document_id"]

    supervisor_token = login(vault_client, *ACME_SUPERVISOR)
    resp = vault_client.delete(f"/vault/documents/{doc_id}", headers=auth_headers(supervisor_token))
    assert resp.status_code == 403
