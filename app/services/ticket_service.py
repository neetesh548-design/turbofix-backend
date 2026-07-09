"""Ticket service — all business logic for creating, processing, and closing tickets.

This is where the heavy lifting from main.py now lives.  The route handler
(webhook_router.py) only parses the HTTP request and calls these methods.
"""

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import BackgroundTasks

from app import config
from app.infrastructure.logging import get_logger
from app.parser import parse_message
from app.repositories.base import EventRepository, MachineRepository, TicketRepository, new_event_id
from app.services import ai_service, fanout_service
from app.sessions import Session, SessionStore

log = get_logger("turbofix.ticket")

_PLACEHOLDER_DESCRIPTION = "(no description provided)"

_CLOSE_RE = re.compile(
    r"(?:close|closed|resolve|resolved|done|fixed|complete|completed|band|bंद)\s*(T[\w-]+)",
    re.IGNORECASE,
)


def _merge_description(existing: str, transcript: str) -> str:
    if not existing or existing == _PLACEHOLDER_DESCRIPTION:
        return transcript
    return f"{existing} | Voice note: {transcript}"


def _log_event(
    events: EventRepository,
    machine_id: str,
    company_code: str,
    ticket_id: str,
    event_type: str,
    actor_phone: str,
    description: str,
    media_type: str = "",
    media_id: str = "",
    language: str = "",
) -> None:
    """Log an event to the MachineEvents tab."""
    try:
        events.append({
            "event_id": new_event_id(),
            "machine_id": machine_id,
            "company_code": company_code,
            "ticket_id": ticket_id,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "actor_phone": actor_phone,
            "description": description,
            "media_type": media_type,
            "media_id": media_id,
            "language": language,
        })
    except Exception as exc:
        log.error("event.log_failed", ticket_id=ticket_id, error=str(exc))


async def _detect_and_store_language(
    ticket_id: str,
    text: str,
    tickets: TicketRepository,
) -> str:
    """Detect language from text and store it on the ticket. Returns the detected language code."""
    if not ai_service.ai_enabled() or not text or text == _PLACEHOLDER_DESCRIPTION:
        return "en"
    try:
        lang = await ai_service.detect_language(text)
        tickets.update_language(ticket_id, lang)
        log.info("language.detected", ticket_id=ticket_id, language=lang)
        return lang
    except Exception as exc:
        log.error("language.detect_failed", ticket_id=ticket_id, error=str(exc))
        return "en"


_role_summaries_cache: dict = {}


async def _summarize_and_store(
    ticket_id: str,
    description: str,
    tickets: TicketRepository,
    new_description: Optional[str] = None,
) -> None:
    if not ai_service.ai_enabled():
        log.info("ai.skipped", reason="not_configured", ticket_id=ticket_id)
        return
    try:
        brief = await ai_service.summarize_issue(description)
        tickets.update_ai_fields(
            ticket_id,
            ai_summary=brief.as_ai_summary(),
            urgency=brief.urgency,
            description=new_description,
        )
        _role_summaries_cache[ticket_id] = {
            "owner": brief.owner_summary,
            "supervisor": brief.supervisor_summary,
            "technician": brief.technician_summary,
        }
        log.info("ai.stored", ticket_id=ticket_id, urgency=brief.urgency)
    except Exception as exc:
        log.error("ai.summarize_failed", ticket_id=ticket_id, error=str(exc))


async def _notify_fanout(
    machine_id: str,
    ticket_id: str,
    tickets: TicketRepository,
    machines: MachineRepository,
) -> None:
    machine = machines.get(machine_id)
    ticket = tickets.get(ticket_id)
    if machine is None or ticket is None:
        log.warning("fanout.missing_data", ticket_id=ticket_id, machine_id=machine_id)
        return
    role_data = _role_summaries_cache.pop(ticket_id, {})
    if role_data:
        ticket = dict(ticket)
        ticket["_owner_summary"] = role_data.get("owner", "")
        ticket["_supervisor_summary"] = role_data.get("supervisor", "")
        ticket["_technician_summary"] = role_data.get("technician", "")
    await fanout_service.notify_ticket(machine, ticket)


async def finish_text_ticket(
    phone: str,
    machine_id: str,
    ticket_id: str,
    description: str,
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
    events: EventRepository,
) -> None:
    """Background tail for a text-triggered ticket: language detection + AI summary + fan-out + event log."""
    lang = await _detect_and_store_language(ticket_id, description, tickets)
    await _summarize_and_store(ticket_id, description, tickets)

    ticket = tickets.get(ticket_id)
    machine = machines.get(machine_id)
    company_code = machine["company_code"] if machine else ""

    _log_event(
        events, machine_id, company_code, ticket_id,
        "ticket_created", phone,
        description or _PLACEHOLDER_DESCRIPTION,
        language=lang,
    )

    if ticket and ticket.get("ai_summary"):
        _log_event(
            events, machine_id, company_code, ticket_id,
            "ai_summary", "system",
            ticket["ai_summary"],
        )

    await _notify_fanout(machine_id, ticket_id, tickets, machines)
    sessions.mark_notified(phone)


async def handle_text_message(
    phone: str,
    text: str,
    background_tasks: BackgroundTasks,
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
    events: EventRepository,
) -> None:
    """Handle an incoming text message — either a new ticket or a closure command."""
    close_match = _CLOSE_RE.search(text)
    if close_match:
        ticket_id_prefix = close_match.group(1)
        background_tasks.add_task(
            handle_close_command, phone, ticket_id_prefix, tickets, machines, events,
        )
        return

    parsed = parse_message(text)
    if parsed is None:
        log.info("message.no_machine_id", phone=phone)
        return

    machine = machines.get(parsed.machine_id)
    if machine is None:
        log.warning("message.unknown_machine", machine_id=parsed.machine_id, phone=phone)
        return

    ticket_id = tickets.next_ticket_id()
    tickets.append({
        "ticket_id": ticket_id,
        "machine_id": parsed.machine_id,
        "company_code": machine["company_code"],
        "machine_name": machine["machine_name"],
        "reported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "reporter_phone": phone,
        "description": parsed.description or _PLACEHOLDER_DESCRIPTION,
        "ai_summary": "",
        "urgency": "",
        "status": "Open",
        "closed_at": "",
        "hours_to_fix": "",
        "voice_note_media_id": "",
        "photo_media_id": "",
        "language": "",
        "closed_by": "",
    })
    sessions.open(phone, ticket_id, parsed.machine_id)
    log.info("ticket.created", ticket_id=ticket_id, machine_id=parsed.machine_id, phone=phone)

    if parsed.description:
        background_tasks.add_task(
            finish_text_ticket, phone, parsed.machine_id, ticket_id,
            parsed.description, sessions, tickets, machines, events,
        )


async def finish_audio_ticket(
    phone: str,
    session: Session,
    media_id: str,
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
    events: EventRepository,
) -> None:
    """Background tail for a voice-note-triggered ticket: transcribe + summarize + fan-out + event log."""
    from app.infrastructure import whatsapp

    machine = machines.get(session.machine_id)
    company_code = machine["company_code"] if machine else ""
    transcript = ""

    if ai_service.ai_enabled():
        try:
            local_path = await whatsapp.download_media(media_id)
            transcript = await ai_service.transcribe_audio(local_path)
            existing = tickets.get(session.ticket_id)
            existing_desc = existing["description"] if existing else ""
            merged = _merge_description(existing_desc, transcript)
            await _summarize_and_store(session.ticket_id, merged, tickets, new_description=merged)
        except Exception as exc:
            log.error("transcription.failed", ticket_id=session.ticket_id, error=str(exc))
    else:
        log.info("transcription.skipped", reason="ai_disabled", ticket_id=session.ticket_id)

    lang = await _detect_and_store_language(
        session.ticket_id, transcript or "(voice note)", tickets,
    )

    _log_event(
        events, session.machine_id, company_code, session.ticket_id,
        "voice_note", phone,
        transcript or "(voice note — transcription unavailable)",
        media_type="audio", media_id=media_id, language=lang,
    )

    ticket = tickets.get(session.ticket_id)
    if ticket and ticket.get("ai_summary"):
        _log_event(
            events, session.machine_id, company_code, session.ticket_id,
            "ai_summary", "system",
            ticket["ai_summary"],
        )

    if not session.notified:
        await _notify_fanout(session.machine_id, session.ticket_id, tickets, machines)
        sessions.mark_notified(phone)


async def handle_audio_message(
    phone: str,
    media_id: str,
    background_tasks: BackgroundTasks,
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
    events: EventRepository,
) -> None:
    """Attach a voice note to the active session's ticket and kick off background work."""
    session = sessions.get(phone)
    if session is None:
        log.warning("audio.no_session", phone=phone, media_id=media_id)
        return

    tickets.attach_voice_note(session.ticket_id, media_id)
    log.info("audio.attached", ticket_id=session.ticket_id, media_id=media_id)

    background_tasks.add_task(
        finish_audio_ticket, phone, session, media_id, sessions, tickets, machines, events,
    )


async def finish_image_ticket(
    phone: str,
    session: Session,
    media_id: str,
    tickets: TicketRepository,
    machines: MachineRepository,
    events: EventRepository,
) -> None:
    """Background tail for an image message: download, analyze, store description, log event."""
    from app.infrastructure import whatsapp

    machine = machines.get(session.machine_id)
    company_code = machine["company_code"] if machine else ""

    tickets.attach_photo(session.ticket_id, media_id)
    image_description = ""

    if ai_service.ai_enabled():
        try:
            local_path = await whatsapp.download_media(media_id)
            image_description = await ai_service.analyze_image(local_path)

            existing = tickets.get(session.ticket_id)
            existing_desc = existing["description"] if existing else ""
            merged = _merge_description(existing_desc, f"[Photo analysis] {image_description}")
            await _summarize_and_store(session.ticket_id, merged, tickets, new_description=merged)
        except Exception as exc:
            log.error("image.analysis_failed", ticket_id=session.ticket_id, error=str(exc))

    _log_event(
        events, session.machine_id, company_code, session.ticket_id,
        "photo", phone,
        image_description or "(photo — analysis unavailable)",
        media_type="image", media_id=media_id,
    )


async def handle_image_message(
    phone: str,
    media_id: str,
    background_tasks: BackgroundTasks,
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
    events: EventRepository,
) -> None:
    """Attach a photo to the active session's ticket and kick off image analysis."""
    session = sessions.get(phone)
    if session is None:
        log.warning("image.no_session", phone=phone, media_id=media_id)
        return

    log.info("image.attached", ticket_id=session.ticket_id, media_id=media_id)

    background_tasks.add_task(
        finish_image_ticket, phone, session, media_id, tickets, machines, events,
    )


async def handle_close_command(
    phone: str,
    ticket_id_input: str,
    tickets: TicketRepository,
    machines: MachineRepository,
    events: EventRepository,
) -> None:
    """Close a ticket and notify all stakeholders + the worker."""
    ticket = tickets.find_by_id_prefix(ticket_id_input)
    if ticket is None:
        log.warning("close.ticket_not_found", input=ticket_id_input, phone=phone)
        return

    ticket_id = ticket["ticket_id"]
    if ticket.get("status") == "Closed":
        log.info("close.already_closed", ticket_id=ticket_id, phone=phone)
        return

    tickets.close_ticket(ticket_id, phone)
    log.info("ticket.closed", ticket_id=ticket_id, closed_by=phone)

    machine_id = ticket.get("machine_id", "")
    company_code = ticket.get("company_code", "")
    machine = machines.get(machine_id)

    _log_event(
        events, machine_id, company_code, ticket_id,
        "ticket_closed", phone,
        f"Ticket closed by {phone}",
    )

    if machine is None:
        log.warning("close.machine_not_found", machine_id=machine_id)
        return

    refreshed_ticket = tickets.get(ticket_id)
    if refreshed_ticket is None:
        return

    worker_lang = refreshed_ticket.get("language") or "en"
    translated = None
    if worker_lang != "en" and ai_service.ai_enabled():
        try:
            machine_name = refreshed_ticket.get("machine_name", "")
            msg = (
                f"Your reported issue for {machine_name} (ticket {ticket_id}) "
                f"has been resolved and closed. Thank you for reporting!"
            )
            translated = await ai_service.translate_message(msg, worker_lang)
        except Exception as exc:
            log.error("close.translate_failed", ticket_id=ticket_id, error=str(exc))

    await fanout_service.notify_closure(
        machine, refreshed_ticket, phone,
        translated_message=translated,
        worker_language=worker_lang,
    )


async def sweep_expired_unnotified(
    sessions: SessionStore,
    tickets: TicketRepository,
    machines: MachineRepository,
) -> None:
    """Fire a fallback fan-out for any session that expired without being notified."""
    for phone, session in sessions.sweep_expired_unnotified():
        log.info(
            "sweep.fallback_fanout",
            ticket_id=session.ticket_id,
            machine_id=session.machine_id,
        )
        await _notify_fanout(session.machine_id, session.ticket_id, tickets, machines)
