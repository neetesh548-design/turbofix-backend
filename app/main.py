import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, Request, Response

from app import config, store, whatsapp_client
from app.ai.summarize import summarize_issue
from app.ai.transcribe import transcribe_audio
from app.fanout import notify_ticket
from app.parser import parse_message
from app.sessions import Session, SessionStore

logger = logging.getLogger("turbofix")
sessions = SessionStore()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    sweep_task = asyncio.create_task(_sweep_loop())
    try:
        yield
    finally:
        sweep_task.cancel()


app = FastAPI(
    title="TurboFix Webhook — Phase 1+2+3+4 (receive, log, transcribe, summarize, fan-out & harden)",
    lifespan=_lifespan,
)

_PLACEHOLDER_DESCRIPTION = "(no description provided)"


def _merge_description(existing: str, transcript: str) -> str:
    if not existing or existing == _PLACEHOLDER_DESCRIPTION:
        return transcript
    return f"{existing} | Voice note: {transcript}"


async def _summarize_and_store(ticket_id: str, description: str, new_description: str = None) -> None:
    """Runs the AI summary for a ticket and writes it back. Any failure (missing
    API key, network error, bad response) is logged and swallowed - a ticket must
    never fail to log just because the AI layer is unavailable."""
    if not config.OPENAI_API_KEY:
        logger.info("OPENAI_API_KEY not set, skipping AI summary for ticket %s", ticket_id)
        return
    try:
        brief = await summarize_issue(description)
        store.update_ai_fields(
            ticket_id,
            ai_summary=brief.as_ai_summary(),
            urgency=brief.urgency,
            description=new_description,
        )
        logger.info("AI summary added to ticket %s (urgency=%s)", ticket_id, brief.urgency)
    except Exception:
        logger.exception("AI summarization failed for ticket %s, leaving it blank", ticket_id)


async def _notify_fanout(machine_id: str, ticket_id: str) -> None:
    """Fans a finished ticket out to its machine's assigned technician and informed
    users. Called once a ticket has reached its "final" state for the message that
    triggered it (a typed description, or a voice note session closing out)."""
    if not config.WHATSAPP_ACCESS_TOKEN or not config.WHATSAPP_PHONE_NUMBER_ID:
        logger.info("WhatsApp send credentials not set, skipping fan-out for ticket %s", ticket_id)
        return

    machine = store.get_machine(machine_id)
    ticket = store.get_ticket(ticket_id)
    if machine is None or ticket is None:
        logger.warning("could not fan out ticket %s (machine or ticket missing)", ticket_id)
        return

    await notify_ticket(machine, ticket)


@app.get("/webhook")
def verify_webhook(request: Request):
    """Meta's one-time handshake when you register the webhook URL."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")

    if mode == "subscribe" and token == config.WHATSAPP_VERIFY_TOKEN:
        return Response(content=challenge, media_type="text/plain")
    return Response(status_code=403)


def _iter_messages(payload: dict):
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                yield message


async def _finish_text_ticket(phone: str, machine_id: str, ticket_id: str, description: str) -> None:
    """The slow tail of a text-triggered ticket (AI summary + fan-out), run as a
    background task so the webhook can ack WhatsApp immediately instead of blocking
    on OpenAI/WhatsApp round-trips."""
    await _summarize_and_store(ticket_id, description)
    await _notify_fanout(machine_id, ticket_id)
    sessions.mark_notified(phone)


async def _handle_text_message(phone: str, text: str, background_tasks: BackgroundTasks) -> None:
    parsed = parse_message(text)
    if parsed is None:
        logger.info("no machine ID found in message from %s, ignoring", phone)
        return

    machine = store.get_machine(parsed.machine_id)
    if machine is None:
        logger.warning("unknown machine_id %s reported by %s", parsed.machine_id, phone)
        return

    ticket_id = store.next_ticket_id()
    store.append_ticket({
        "ticket_id": ticket_id,
        "machine_id": parsed.machine_id,
        "company_code": machine["company_code"],
        "machine_name": machine["machine_name"],
        "reported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "reporter_phone": phone,
        "description": parsed.description or "(no description provided)",
        "ai_summary": "",
        "urgency": "",
        "status": "Open",
        "closed_at": "",
        "hours_to_fix": "",
        "voice_note_media_id": "",
    })
    sessions.open(phone, ticket_id, parsed.machine_id)
    logger.info("logged ticket %s for %s from %s", ticket_id, parsed.machine_id, phone)

    if parsed.description:
        # A typed description is the common "no voice note coming" case, so this is
        # the ticket's final content - fan it out now (in the background) rather
        # than waiting on a session TTL that may never see a follow-up voice note.
        background_tasks.add_task(_finish_text_ticket, phone, parsed.machine_id, ticket_id, parsed.description)


async def _finish_audio_ticket(phone: str, session: Session, media_id: str) -> None:
    """The slow tail of a voice-note-triggered ticket (transcribe, summarize,
    fan-out), run as a background task for the same reason as `_finish_text_ticket`."""
    if not config.OPENAI_API_KEY:
        logger.info("OPENAI_API_KEY not set, skipping transcription for ticket %s", session.ticket_id)
    else:
        try:
            local_path = await whatsapp_client.download_media(media_id)
            transcript = await transcribe_audio(local_path)
        except Exception:
            logger.exception("transcription failed for ticket %s, leaving description as-is", session.ticket_id)
        else:
            ticket = store.get_ticket(session.ticket_id)
            existing_description = ticket["description"] if ticket else ""
            merged_description = _merge_description(existing_description, transcript)
            await _summarize_and_store(session.ticket_id, merged_description, new_description=merged_description)

    # A voice note always signals the worker is done describing the issue, so this
    # is a good point to fan out - regardless of whether transcription succeeded.
    # Skip it if the text message already triggered fan-out for this same session,
    # so a worker who both types a description and sends a voice note isn't
    # double-notified.
    if not session.notified:
        await _notify_fanout(session.machine_id, session.ticket_id)
        sessions.mark_notified(phone)


async def _handle_audio_message(phone: str, media_id: str, background_tasks: BackgroundTasks) -> None:
    session = sessions.get(phone)
    if session is None:
        logger.warning(
            "voice note from %s with no recent machine-ID message, dropping (media_id=%s)",
            phone, media_id,
        )
        return

    store.attach_voice_note(session.ticket_id, media_id)
    logger.info("attached voice note %s to ticket %s", media_id, session.ticket_id)

    background_tasks.add_task(_finish_audio_ticket, phone, session, media_id)


async def _sweep_once() -> None:
    """Fires a fallback fan-out for any session that expired without ever being
    notified - e.g. a bare machine-ID text message with no follow-up voice note.
    Without this, that ticket would sit logged but silently un-notified forever."""
    for phone, session in sessions.sweep_expired_unnotified():
        logger.info(
            "ticket %s (machine %s) timed out waiting for a voice note without being "
            "notified, sending fallback fan-out",
            session.ticket_id, session.machine_id,
        )
        await _notify_fanout(session.machine_id, session.ticket_id)


async def _sweep_loop() -> None:
    while True:
        await asyncio.sleep(config.SESSION_SWEEP_INTERVAL_SECONDS)
        await _sweep_once()


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    for message in _iter_messages(payload):
        phone = message.get("from", "")
        msg_type = message.get("type")

        if msg_type == "text":
            await _handle_text_message(phone, message.get("text", {}).get("body", ""), background_tasks)
        elif msg_type == "audio":
            await _handle_audio_message(phone, message.get("audio", {}).get("id", ""), background_tasks)
        else:
            logger.info("ignoring unsupported message type %s from %s", msg_type, phone)

    # WhatsApp expects a fast 200 OK regardless of what we did with the message.
    return Response(status_code=200)
