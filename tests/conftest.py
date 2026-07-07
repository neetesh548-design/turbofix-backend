import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config

TRACKER_SOURCE = Path(__file__).resolve().parent.parent.parent / "TurboFix-Tracker.xlsx"

# Known sample credentials seeded by build_tracker.py, reused across vault tests.
ACME_OWNER = ("rakesh@acmeforge.example", "AcmeOwner@2026")
ACME_MAINTENANCE_HEAD = ("vikram@acmeforge.example", "AcmeMaint@2026")
ACME_SUPERVISOR = ("sunil@acmeforge.example", "AcmeSuper@2026")
BETA_OWNER = ("meena@betaprecision.example", "BetaOwner@2026")


@pytest.fixture
def vault_client(tmp_path, monkeypatch):
    """A TestClient wired to a throwaway copy of the tracker (never the real one)
    and a throwaway document-store directory for uploads."""
    dest = tmp_path / "TurboFix-Tracker-test.xlsx"
    shutil.copy(TRACKER_SOURCE, dest)
    monkeypatch.setattr(config, "TRACKER_XLSX_PATH", str(dest))

    doc_store_dir = tmp_path / "document_store"
    doc_store_dir.mkdir()
    monkeypatch.setattr(config, "DOCUMENT_STORE_DIR", doc_store_dir)

    from app import main

    return TestClient(main.app)


def login(client, identifier: str, password: str) -> str:
    resp = client.post("/auth/login", json={"identifier": identifier, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
