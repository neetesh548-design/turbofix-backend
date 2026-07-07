import asyncio

import pytest

from app import fanout, whatsapp_client


def _machine(**overrides):
    machine = {
        "machine_name": "CNC Lathe 1",
        "assigned_technician_phone": "919900011111",
        "informed_phones": ["919900022222", "919900033333"],
    }
    machine.update(overrides)
    return machine


def _ticket(**overrides):
    ticket = {
        "ticket_id": "T20260707-abcd",
        "machine_name": "CNC Lathe 1",
        "description": "spindle making loud noise",
        "ai_summary": "",
        "urgency": "",
        "reporter_phone": "919900099999",
    }
    ticket.update(overrides)
    return ticket


def test_notify_ticket_sends_to_technician_and_informed_users(monkeypatch):
    sent = []

    async def fake_send_template_message(to, params):
        sent.append((to, params))

    monkeypatch.setattr(whatsapp_client, "send_template_message", fake_send_template_message)

    asyncio.run(fanout.notify_ticket(_machine(), _ticket()))

    assert [to for to, _ in sent] == ["919900011111", "919900022222", "919900033333"]


def test_notify_ticket_prefers_ai_summary_over_raw_description(monkeypatch):
    sent = []

    async def fake_send_template_message(to, params):
        sent.append((to, params))

    monkeypatch.setattr(whatsapp_client, "send_template_message", fake_send_template_message)

    ticket = _ticket(ai_summary="Likely cause: worn bearing | Suggested action: replace it")
    asyncio.run(fanout.notify_ticket(_machine(), ticket))

    _, params = sent[0]
    assert params == [
        "CNC Lathe 1",
        "T20260707-abcd",
        "Likely cause: worn bearing | Suggested action: replace it",
        "Medium",
        "919900099999",
    ]


def test_notify_ticket_skips_when_no_recipients(monkeypatch):
    called = False

    async def fake_send_template_message(to, params):
        nonlocal called
        called = True

    monkeypatch.setattr(whatsapp_client, "send_template_message", fake_send_template_message)

    machine = _machine(assigned_technician_phone=None, informed_phones=[])
    asyncio.run(fanout.notify_ticket(machine, _ticket()))

    assert called is False


def test_notify_ticket_one_recipient_failure_does_not_block_others(monkeypatch):
    sent = []

    async def fake_send_template_message(to, params):
        if to == "919900011111":
            raise RuntimeError("simulated send failure")
        sent.append(to)

    monkeypatch.setattr(whatsapp_client, "send_template_message", fake_send_template_message)

    asyncio.run(fanout.notify_ticket(_machine(), _ticket()))

    assert sent == ["919900022222", "919900033333"]
