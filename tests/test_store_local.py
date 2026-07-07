import shutil
from pathlib import Path

import pytest

from app import config

TRACKER_SOURCE = Path(__file__).resolve().parent.parent.parent / "TurboFix-Tracker.xlsx"


@pytest.fixture
def tracker_copy(tmp_path, monkeypatch):
    dest = tmp_path / "TurboFix-Tracker-test.xlsx"
    shutil.copy(TRACKER_SOURCE, dest)
    monkeypatch.setattr(config, "TRACKER_XLSX_PATH", str(dest))
    return dest


def test_load_machines_reads_expected_rows(tracker_copy):
    from app import store_local

    machines = store_local.load_machines()
    assert "TF-ACME3-M001" in machines
    assert machines["TF-ACME3-M001"]["company_code"] == "ACME3"
    assert machines["TF-ACME3-M001"]["assigned_technician_phone"] == "+919812340001"
    assert machines["TF-ACME3-M001"]["informed_phones"] == ["+919812340010", "+919812340011"]


def test_load_machines_is_cached_within_ttl(tracker_copy, monkeypatch):
    from app import store_local

    store_local._machines_cache = None
    store_local._machines_cache_key = None
    store_local._machines_cache_at = 0.0

    first = store_local.load_machines()

    # remove the underlying file to prove a second call within the TTL doesn't
    # re-read it - if it did, this would raise instead of returning cached data
    tracker_copy.unlink()

    second = store_local.load_machines()
    assert second == first


def test_load_machines_refreshes_after_ttl_expires(tracker_copy, monkeypatch):
    from app import store_local

    monkeypatch.setattr(config, "MACHINES_CACHE_TTL_SECONDS", 0)
    store_local._machines_cache = None
    store_local._machines_cache_key = None
    store_local._machines_cache_at = 0.0

    first = store_local.load_machines()
    first["TF-ACME3-M001"]["machine_name"] = "mutated in the returned dict, not the file"

    second = store_local.load_machines()
    assert second["TF-ACME3-M001"]["machine_name"] == "CNC Lathe 1"


def test_append_ticket_adds_a_row(tracker_copy):
    from app import store_local

    row = {
        "ticket_id": "TTEST001",
        "machine_id": "TF-ACME3-M001",
        "company_code": "ACME3",
        "machine_name": "CNC Lathe 1",
        "reported_at": "2026-07-07 12:00",
        "reporter_phone": "+919900099999",
        "description": "test description",
        "ai_summary": "",
        "urgency": "",
        "status": "Open",
        "closed_at": "",
        "hours_to_fix": "",
        "voice_note_media_id": "",
    }
    store_local.append_ticket(row)

    import openpyxl
    wb = openpyxl.load_workbook(tracker_copy)
    ws = wb["Tickets"]
    last_row = [c.value for c in ws[ws.max_row]]
    assert last_row[0] == "TTEST001"
    assert last_row[1] == "TF-ACME3-M001"
    assert last_row[6] == "test description"


def test_attach_voice_note_updates_matching_row(tracker_copy):
    from app import store_local

    store_local.append_ticket({
        "ticket_id": "TTEST002", "machine_id": "TF-ACME3-M001", "company_code": "ACME3",
        "machine_name": "CNC Lathe 1", "reported_at": "2026-07-07 12:00",
        "reporter_phone": "+919900099999", "description": "d", "ai_summary": "",
        "urgency": "", "status": "Open", "closed_at": "", "hours_to_fix": "",
        "voice_note_media_id": "",
    })
    found = store_local.attach_voice_note("TTEST002", "wamid.HBgL...")
    assert found is True

    import openpyxl
    wb = openpyxl.load_workbook(tracker_copy)
    ws = wb["Tickets"]
    last_row = [c.value for c in ws[ws.max_row]]
    assert last_row[12] == "wamid.HBgL..."


def test_attach_voice_note_returns_false_for_unknown_ticket(tracker_copy):
    from app import store_local

    assert store_local.attach_voice_note("NOPE", "media123") is False


def test_next_ticket_id_is_unique():
    from app import store_local

    a = store_local.next_ticket_id()
    b = store_local.next_ticket_id()
    assert a != b
    assert a.startswith("T")


def test_get_ticket_returns_matching_row(tracker_copy):
    from app import store_local

    store_local.append_ticket({
        "ticket_id": "TTEST003", "machine_id": "TF-ACME3-M001", "company_code": "ACME3",
        "machine_name": "CNC Lathe 1", "reported_at": "2026-07-07 12:00",
        "reporter_phone": "+919900099999", "description": "original description",
        "ai_summary": "", "urgency": "", "status": "Open", "closed_at": "",
        "hours_to_fix": "", "voice_note_media_id": "",
    })
    ticket = store_local.get_ticket("TTEST003")
    assert ticket is not None
    assert ticket["description"] == "original description"
    assert ticket["machine_id"] == "TF-ACME3-M001"


def test_get_ticket_returns_none_for_unknown_id(tracker_copy):
    from app import store_local

    assert store_local.get_ticket("NOPE") is None


def test_update_ai_fields_sets_summary_urgency_and_description(tracker_copy):
    from app import store_local

    store_local.append_ticket({
        "ticket_id": "TTEST004", "machine_id": "TF-ACME3-M001", "company_code": "ACME3",
        "machine_name": "CNC Lathe 1", "reported_at": "2026-07-07 12:00",
        "reporter_phone": "+919900099999", "description": "(no description provided)",
        "ai_summary": "", "urgency": "", "status": "Open", "closed_at": "",
        "hours_to_fix": "", "voice_note_media_id": "",
    })
    found = store_local.update_ai_fields(
        "TTEST004",
        ai_summary="Likely cause: worn bearing | Suggested action: inspect bearing",
        urgency="High",
        description="spindle making loud noise",
    )
    assert found is True

    ticket = store_local.get_ticket("TTEST004")
    assert ticket["ai_summary"] == "Likely cause: worn bearing | Suggested action: inspect bearing"
    assert ticket["urgency"] == "High"
    assert ticket["description"] == "spindle making loud noise"


def test_update_ai_fields_returns_false_for_unknown_ticket(tracker_copy):
    from app import store_local

    assert store_local.update_ai_fields("NOPE", "summary", "Low") is False
