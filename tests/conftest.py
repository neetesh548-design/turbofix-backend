import os
import shutil
from pathlib import Path

# Tests always run against the local xlsx store with the AI layer and WhatsApp off,
# regardless of what the developer's .env says — otherwise a .env pointed at the
# live Google Sheet (Phase 3) would make the suite write test rows into live data
# and fire real network calls. Must be set before any `app` import: the stores bind
# their backend at import time from TICKET_STORE, and load_dotenv() does not
# override variables that are already in the environment.
os.environ["TICKET_STORE"] = "local"
os.environ["GEMINI_API_KEY"] = ""
os.environ["OPENAI_API_KEY"] = ""
os.environ["WHATSAPP_ACCESS_TOKEN"] = ""
os.environ["WHATSAPP_PHONE_NUMBER_ID"] = ""

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
