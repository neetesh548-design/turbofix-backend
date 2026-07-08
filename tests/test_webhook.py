import asyncio
import shutil
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import config

TRACKER_SOURCE = Path(__file__).resolve().parent.parent.parent / "TurboFix-Tracker.xlsx"


def _text_payload(phone: str, body: str) -> dict:
    return {
        "entry": [{"changes": [{"value": {"messages": [
            {"from": phone, "id": "wamid.text1", "type": "text", "text": {"body": body}}
        ]}}]}]
    }


def _audio_payload(phone: str, media_id: str) -> dict:
    return {
        "entry": [{"changes": [{"value": {"messages": [
            {"from": phone, "id": "wamid.audio1", "type": "audio", "audio": {"id": media_id, "mime_type": "audio/ogg"}}
        ]}}]}]
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    dest = tmp_path / "TurboFix-Tracker-test.xlsx"
    shutil.copy(TRACKER_SOURCE, dest)
    monkeypatch.setattr(config, "TRACKER_XLSX_PATH", str(dest))
    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", "test-verify-token")

    from app import main, whatsapp_client

    async def fake_download_media(media_id):
        return f"/fake/media_store/{media_id}"

    monkeypatch.setattr(whatsapp_client, "download_media", fake_download_media)
    # main.py already imported download_media indirectly via the whatsapp_client module
    # reference, so patching the module attribute above is sufficient.

    return TestClient(main.app), dest


def _last_ticket_row(tracker_path):
    wb = openpyxl.load_workbook(tracker_path)
    ws = wb["Tickets"]
    return [c.value for c in ws[ws.max_row]]


def test_webhook_verification_handshake(client):
    test_client, _ = client
    resp = test_client.get("/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "test-verify-token",
        "hub.challenge": "12345",
    })
    assert resp.status_code == 200
    assert resp.text == "12345"


def test_webhook_verification_rejects_wrong_token(client):
    test_client, _ = client
    resp = test_client.get("/webhook", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "wrong",
        "hub.challenge": "12345",
    })
    assert resp.status_code == 403


def test_text_message_with_known_machine_logs_ticket(client):
    test_client, tracker_path = client
    resp = test_client.post("/webhook", json=_text_payload(
        "919900012345", "Issue with TF-ACME3-M001: spindle making loud noise"
    ))
    assert resp.status_code == 200

    row = _last_ticket_row(tracker_path)
    assert row[1] == "TF-ACME3-M001"
    assert row[2] == "ACME3"
    assert row[3] == "CNC Lathe 1"
    assert row[5] == "919900012345"
    assert row[6] == "spindle making loud noise"
    assert row[9] == "Open"
    assert row[12] in ("", None)


def test_text_message_with_unknown_machine_is_ignored(client):
    test_client, tracker_path = client
    before_rows = openpyxl.load_workbook(tracker_path)["Tickets"].max_row

    resp = test_client.post("/webhook", json=_text_payload(
        "919900012345", "Issue with TF-ZZZZ-M999: something"
    ))
    assert resp.status_code == 200

    after_rows = openpyxl.load_workbook(tracker_path)["Tickets"].max_row
    assert after_rows == before_rows


def test_voice_note_after_text_gets_attached_to_ticket(client):
    test_client, tracker_path = client
    test_client.post("/webhook", json=_text_payload(
        "919900012345", "Issue with TF-ACME3-M001: spindle making loud noise"
    ))
    ticket_id_before = _last_ticket_row(tracker_path)[0]

    resp = test_client.post("/webhook", json=_audio_payload("919900012345", "wamid.AUDIO123"))
    assert resp.status_code == 200

    row = _last_ticket_row(tracker_path)
    assert row[0] == ticket_id_before
    assert row[12] == "wamid.AUDIO123"


def test_voice_note_without_prior_text_is_dropped(client):
    test_client, tracker_path = client
    before_rows = openpyxl.load_workbook(tracker_path)["Tickets"].max_row

    resp = test_client.post("/webhook", json=_audio_payload("919900099999", "wamid.ORPHAN"))
    assert resp.status_code == 200

    after_rows = openpyxl.load_workbook(tracker_path)["Tickets"].max_row
    assert after_rows == before_rows


class _FakeBrief:
    def __init__(self, likely_cause, urgency, suggested_action):
        self.likely_cause = likely_cause
        self.urgency = urgency
        self.suggested_action = suggested_action

    def as_ai_summary(self):
        return f"Likely cause: {self.likely_cause} | Suggested action: {self.suggested_action}"


def test_text_message_with_description_gets_ai_summary(client, monkeypatch):
    test_client, tracker_path = client
    monkeypatch.setattr(config, "OPENAI_API_KEY", "fake-key")

    from app import main

    async def fake_summarize_issue(description):
        assert description == "spindle making loud noise"
        return _FakeBrief("worn bearing", "High", "inspect and replace bearing")

    monkeypatch.setattr(main.ai, "summarize_issue", fake_summarize_issue)

    resp = test_client.post("/webhook", json=_text_payload(
        "919900012345", "Issue with TF-ACME3-M001: spindle making loud noise"
    ))
    assert resp.status_code == 200

    row = _last_ticket_row(tracker_path)
    assert row[7] == "Likely cause: worn bearing | Suggested action: inspect and replace bearing"
    assert row[8] == "High"
    assert row[6] == "spindle making loud noise"  # description untouched for text-only path


def test_voice_note_gets_transcribed_and_summarized(client, monkeypatch):
    test_client, tracker_path = client
    monkeypatch.setattr(config, "OPENAI_API_KEY", "fake-key")

    from app import main

    async def fake_transcribe_audio(path):
        return "compressor tripping the breaker"

    async def fake_summarize_issue(description):
        assert description == "compressor tripping the breaker"
        return _FakeBrief("motor overload", "High", "have an electrician inspect it")

    monkeypatch.setattr(main.ai, "transcribe_audio", fake_transcribe_audio)
    monkeypatch.setattr(main.ai, "summarize_issue", fake_summarize_issue)

    # bare ID, no typed description -> placeholder, so the transcript should replace it
    test_client.post("/webhook", json=_text_payload("919900012345", "TF-ACME3-M001"))
    resp = test_client.post("/webhook", json=_audio_payload("919900012345", "wamid.AUDIO999"))
    assert resp.status_code == 200

    row = _last_ticket_row(tracker_path)
    assert row[6] == "compressor tripping the breaker"
    assert row[7] == "Likely cause: motor overload | Suggested action: have an electrician inspect it"
    assert row[8] == "High"
    assert row[12] == "wamid.AUDIO999"


def test_ai_failure_is_swallowed_and_ticket_stays_logged(client, monkeypatch):
    test_client, tracker_path = client
    monkeypatch.setattr(config, "OPENAI_API_KEY", "fake-key")

    from app import main

    async def failing_summarize_issue(description):
        raise RuntimeError("simulated API outage")

    monkeypatch.setattr(main.ai, "summarize_issue", failing_summarize_issue)

    resp = test_client.post("/webhook", json=_text_payload(
        "919900012345", "Issue with TF-ACME3-M001: spindle making loud noise"
    ))
    assert resp.status_code == 200

    row = _last_ticket_row(tracker_path)
    assert row[1] == "TF-ACME3-M001"
    assert row[7] in ("", None)  # ai_summary left blank, not crashed


def test_no_ai_keys_skips_ai_but_still_logs_ticket(client, monkeypatch):
    test_client, tracker_path = client
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")

    resp = test_client.post("/webhook", json=_text_payload(
        "919900012345", "Issue with TF-ACME3-M001: spindle making loud noise"
    ))
    assert resp.status_code == 200
    row = _last_ticket_row(tracker_path)
    assert row[1] == "TF-ACME3-M001"


def _enable_fanout_credentials(monkeypatch):
    monkeypatch.setattr(config, "WHATSAPP_ACCESS_TOKEN", "fake-token")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_NUMBER_ID", "1234567890")


def test_fanout_skipped_without_whatsapp_send_credentials(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(config, "WHATSAPP_ACCESS_TOKEN", "")
    monkeypatch.setattr(config, "WHATSAPP_PHONE_NUMBER_ID", "")

    from app import main

    called = False

    async def fake_notify_ticket(machine, ticket):
        nonlocal called
        called = True

    monkeypatch.setattr(main, "notify_ticket", fake_notify_ticket)

    resp = test_client.post("/webhook", json=_text_payload(
        "919900012345", "Issue with TF-ACME3-M001: spindle making loud noise"
    ))
    assert resp.status_code == 200
    assert called is False


def test_text_with_description_triggers_fanout_to_technician_and_informed(client, monkeypatch):
    test_client, _ = client
    _enable_fanout_credentials(monkeypatch)

    from app import main

    calls = []

    async def fake_notify_ticket(machine, ticket):
        calls.append((machine, ticket))

    monkeypatch.setattr(main, "notify_ticket", fake_notify_ticket)

    resp = test_client.post("/webhook", json=_text_payload(
        "919900012345", "Issue with TF-ACME3-M001: spindle making loud noise"
    ))
    assert resp.status_code == 200

    assert len(calls) == 1
    machine, ticket = calls[0]
    assert machine["assigned_technician_phone"] == "+919812340001"
    assert machine["informed_phones"] == ["+919812340010", "+919812340011"]
    assert ticket["machine_id"] == "TF-ACME3-M001"
    assert ticket["description"] == "spindle making loud noise"


def test_bare_id_text_does_not_fanout_until_voice_note_arrives(client, monkeypatch):
    test_client, _ = client
    _enable_fanout_credentials(monkeypatch)

    from app import main

    calls = []

    async def fake_notify_ticket(machine, ticket):
        calls.append((machine, ticket))

    monkeypatch.setattr(main, "notify_ticket", fake_notify_ticket)

    test_client.post("/webhook", json=_text_payload("919900012345", "TF-ACME3-M001"))
    assert calls == []

    resp = test_client.post("/webhook", json=_audio_payload("919900012345", "wamid.AUDIO1"))
    assert resp.status_code == 200
    assert len(calls) == 1


def test_text_then_voice_note_only_fans_out_once(client, monkeypatch):
    test_client, _ = client
    _enable_fanout_credentials(monkeypatch)

    from app import main

    calls = []

    async def fake_notify_ticket(machine, ticket):
        calls.append((machine, ticket))

    monkeypatch.setattr(main, "notify_ticket", fake_notify_ticket)

    test_client.post("/webhook", json=_text_payload(
        "919900012345", "Issue with TF-ACME3-M001: spindle making loud noise"
    ))
    assert len(calls) == 1

    resp = test_client.post("/webhook", json=_audio_payload("919900012345", "wamid.AUDIO2"))
    assert resp.status_code == 200
    assert len(calls) == 1  # still just the one fan-out from the text message


def test_fanout_fires_even_when_transcription_fails(client, monkeypatch):
    test_client, _ = client
    _enable_fanout_credentials(monkeypatch)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "fake-key")

    from app import main

    async def failing_transcribe(path):
        raise RuntimeError("simulated transcription outage")

    calls = []

    async def fake_notify_ticket(machine, ticket):
        calls.append((machine, ticket))

    monkeypatch.setattr(main.ai, "transcribe_audio", failing_transcribe)
    monkeypatch.setattr(main, "notify_ticket", fake_notify_ticket)

    test_client.post("/webhook", json=_text_payload("919900012345", "TF-ACME3-M001"))
    resp = test_client.post("/webhook", json=_audio_payload("919900012345", "wamid.AUDIO3"))

    assert resp.status_code == 200
    assert len(calls) == 1


def test_sweep_fans_out_orphaned_bare_id_session(client, monkeypatch):
    test_client, _ = client
    _enable_fanout_credentials(monkeypatch)

    from app import main

    calls = []

    async def fake_notify_ticket(machine, ticket):
        calls.append((machine, ticket))

    monkeypatch.setattr(main, "notify_ticket", fake_notify_ticket)

    # bare ID, no voice note ever follows - ticket would otherwise sit un-notified
    test_client.post("/webhook", json=_text_payload("919900054321", "TF-ACME3-M001"))
    assert calls == []

    # simulate the session having expired without a follow-up voice note
    session = main.sessions._sessions["919900054321"]
    session.created_at -= config.SESSION_TTL_SECONDS + 1

    asyncio.run(main._sweep_once())

    assert len(calls) == 1
    machine, ticket = calls[0]
    assert ticket["machine_id"] == "TF-ACME3-M001"
    assert "919900054321" not in main.sessions._sessions


def test_sweep_does_not_refanout_already_notified_sessions(client, monkeypatch):
    test_client, _ = client
    _enable_fanout_credentials(monkeypatch)

    from app import main

    calls = []

    async def fake_notify_ticket(machine, ticket):
        calls.append((machine, ticket))

    monkeypatch.setattr(main, "notify_ticket", fake_notify_ticket)

    test_client.post("/webhook", json=_text_payload(
        "919900054322", "Issue with TF-ACME3-M001: spindle making loud noise"
    ))
    assert len(calls) == 1  # already fanned out via the typed description

    session = main.sessions._sessions["919900054322"]
    session.created_at -= config.SESSION_TTL_SECONDS + 1

    asyncio.run(main._sweep_once())

    assert len(calls) == 1  # sweep did not fire a second, duplicate notification
