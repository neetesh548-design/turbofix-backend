# TurboFix Backend — Phase 1+2+3+4 (receive, log, transcribe, summarize, fan-out & harden)

FastAPI webhook that receives WhatsApp messages, parses the machine ID out of the
pre-filled QR message, logs a ticket row, transcribes/summarizes any voice note into a
structured brief (Phase 2), notifies the machine's assigned technician and informed
users over WhatsApp (Phase 3), and (Phase 4/harden) does this without blocking WhatsApp's
webhook ack, without silently dropping orphaned tickets, and without re-reading the
Machines tab on every message.

## What it does

1. `GET /webhook` — the one-time handshake Meta requires when you register the webhook
   URL (checks `hub.verify_token` against `WHATSAPP_VERIFY_TOKEN`, echoes back `hub.challenge`).
2. `POST /webhook` — receives incoming messages:
   - **Text message** containing a `TF-{companyCode}-{machineCode}` ID → looks the
     machine up, logs a new "Open" ticket row, and opens a short-lived (15 min default)
     session for that sender's phone number. If the message includes a typed description,
     it's immediately summarized (see Phase 2 below).
   - **Voice note (audio message)** arriving while that session is still open → downloaded
     via the Graph API, its `media_id` attached to the ticket, then transcribed and
     summarized (Phase 2). The transcript becomes the ticket's `description` (replacing
     the placeholder if nothing was typed, or appended if something was).
   - Anything else (unknown machine ID, voice note with no recent text, other message
     types) is logged and dropped — deliberately, rather than guessed at.

## Phase 2 — AI layer (transcribe + summarize)

Given a description (typed or transcribed from a voice note), the backend calls OpenAI
to produce a structured brief and writes it into the `ai_summary` and `urgency` columns:

- **Transcription** (`app/ai/transcribe.py`) — OpenAI's audio transcription endpoint,
  model set by `OPENAI_TRANSCRIBE_MODEL` (default `gpt-4o-mini-transcribe`, per the
  cost model in `../progress.md`).
- **Summarization** (`app/ai/summarize.py`) — a chat completion asking for JSON with
  `likely_cause`, `urgency` (Low/Medium/High), and `suggested_action`, model set by
  `OPENAI_CHAT_MODEL` (default `gpt-4.1-nano`, the "nano-tier" model already priced out
  in the cost model).

**Graceful degradation is load-bearing here, not optional:** if `OPENAI_API_KEY` isn't
set, or any OpenAI call fails for any reason, the ticket still gets logged — it just
keeps blank `ai_summary`/`urgency` (and, for a voice note, the raw `media_id` without a
transcript). The webhook must never fail because the AI layer is down; that's why every
AI call site is wrapped and logs-and-swallows rather than propagating.

## Phase 3 — fan-out

Once a ticket reaches its "final" content for the message that triggered it, the
backend notifies the machine's `assigned_technician_phone` and any `informed_phone_*`
recipients (`app/fanout.py`, sending via `app/whatsapp_client.py`):

- A **typed description** is treated as final immediately (the common "no voice note
  coming" case) — fan-out fires right after that message is summarized.
- A **voice note** always fans out once processed (transcribed+summarized, or left
  as-is if transcription failed/was skipped) — it signals the worker is done
  describing the issue.
- If a worker does both (types a description, then also sends a voice note), only the
  first of the two fans out; the second just enriches the ticket's `description` without
  sending a second notification. This is tracked in-memory via the same per-phone
  session used to attach voice notes (`app/sessions.py`'s `notified` flag).

Fan-out recipients haven't messaged TurboFix themselves, so per Meta's policy this must
be a **pre-approved WhatsApp message template** (free-form text isn't allowed outside a
24h customer-service window) — `WHATSAPP_TICKET_TEMPLATE_NAME` must match a template
already approved in Meta Business Manager, with 5 body placeholders in this order:
machine name, ticket ID, brief (AI summary if available, else raw description),
urgency, reporter phone.

**Graceful degradation applies here too:** if `WHATSAPP_ACCESS_TOKEN` or
`WHATSAPP_PHONE_NUMBER_ID` isn't set, fan-out is skipped (logged, not fatal) and the
ticket still logs/summarizes normally. Each recipient send is independent — one
recipient's failure is logged and doesn't block sending to the others or fail the
ticket.

## Phase 4 — harden

Three changes that don't alter behavior for a worker, only reliability/performance
under real WhatsApp traffic:

- **Background processing.** AI summarization/transcription and fan-out now run as a
  FastAPI `BackgroundTask` instead of inline in the request. The webhook logs/attaches
  the ticket synchronously (fast, local I/O) and returns `200 OK` to WhatsApp
  immediately, then finishes the slow OpenAI/WhatsApp-API calls afterwards. WhatsApp
  expects a fast ack and will retry/flag slow webhooks, so this matters once real
  traffic hits.
- **Orphaned-ticket sweep.** A background loop (`app/main.py`'s `_sweep_loop`,
  interval `SESSION_SWEEP_INTERVAL_SECONDS`, default 60s) periodically checks for
  sessions that expired without ever being fanned out — e.g. a bare machine-ID text
  message where the worker never sent a follow-up voice note — and fires a fallback
  fan-out for them instead of leaving that ticket silently un-notified forever
  (`SessionStore.sweep_expired_unnotified()` in `app/sessions.py`).
- **Machines-tab caching.** `store_local.load_machines()` / `store_sheets.load_machines()`
  now cache their result for `MACHINES_CACHE_TTL_SECONDS` (default 60s) instead of
  re-reading the whole tab/Sheet on every single incoming message. A change to a
  machine's assignment can take up to that long to take effect — an acceptable
  trade-off at pilot scale, called out explicitly rather than silently assumed.

## Running locally (no credentials needed)

By default `TICKET_STORE=local`, which reads/writes directly to
`../TurboFix-Tracker.xlsx` via `openpyxl` — no Google or Meta credentials required to
try it out.

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # set WHATSAPP_VERIFY_TOKEN to anything, leave the rest blank
uvicorn app.main:app --reload
```

Simulate an incoming ticket without a real WhatsApp number:

```bash
curl -X POST http://127.0.0.1:8000/webhook -H "Content-Type: application/json" -d '{
  "entry": [{"changes": [{"value": {"messages": [
    {"from": "919900012345", "id": "wamid.test1", "type": "text",
     "text": {"body": "Issue with TF-ACME3-M001: spindle making loud noise"}}
  ]}}]}]
}'
```

Then check `../TurboFix-Tracker.xlsx` — a new row should appear in the `Tickets` tab.
This exact payload shape is what Meta's Cloud API actually posts to your webhook.

To also see the AI layer run, set `OPENAI_API_KEY` in `.env` before starting the
server — with a real key, the ticket above will get `ai_summary`/`urgency` filled in
within a few seconds of the request.

## Running tests

```bash
python3 -m pytest tests/ -q
```

Tests never touch the real `TurboFix-Tracker.xlsx` — each test copies it into a pytest
`tmp_path` first. WhatsApp media downloads and all OpenAI calls (transcription,
summarization) are mocked in tests — no real network calls, no API key needed to run
the suite.

## Going to production

To switch from the local xlsx store to a live Google Sheet:

1. Create a Google Cloud service account, enable the Sheets API, download its JSON key.
2. Share the target Google Sheet (built from `TurboFix-Tracker.xlsx`) with the service
   account's email address (Editor access).
3. Set in `.env`:
   ```
   TICKET_STORE=sheets
   GOOGLE_SHEET_ID=<the sheet ID from its URL>
   GOOGLE_SERVICE_ACCOUNT_FILE=/path/to/service-account.json
   ```

To actually receive messages from WhatsApp (not just simulate them):

1. Create a Meta developer app with the WhatsApp product, get a test number + access token.
2. Set `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, and pick your own
   `WHATSAPP_VERIFY_TOKEN` in `.env`.
3. Deploy this app somewhere with a public HTTPS URL (see `../progress.md` open
   questions for hosting options — not yet evaluated) and register `https://<your-host>/webhook`
   in the Meta app's webhook settings using that same verify token.

To turn on the AI layer, get an OpenAI API key and set `OPENAI_API_KEY` in `.env` —
`OPENAI_TRANSCRIBE_MODEL`/`OPENAI_CHAT_MODEL` default to the models already priced out
in `../progress.md`'s cost model, so leave them unless you're deliberately changing the
cost basis.

To turn on fan-out, get the WhatsApp Cloud API credentials above **and** get
`WHATSAPP_TICKET_TEMPLATE_NAME` approved in Meta Business Manager first — Meta rejects
template sends for unapproved names, and approval can take up to 24h.

## Known limitations (by design, not bugs)

- A voice note that arrives more than `SESSION_TTL_SECONDS` (default 900s) after its
  matching text message, or with no matching text message at all, is dropped rather
  than guessed at.
- If OpenAI is misconfigured or down, tickets still log correctly but `ai_summary`/
  `urgency` stay blank — check the `turbofix` logger for `AI summarization failed` or
  `transcription failed` warnings if this happens unexpectedly.
- The orphaned-ticket sweep (`SESSION_SWEEP_INTERVAL_SECONDS`, default 60s) means a
  bare-ID ticket with no follow-up voice note gets fanned out up to ~`SESSION_TTL_SECONDS
  + SESSION_SWEEP_INTERVAL_SECONDS` after being reported, not instantly — acceptable at
  pilot scale since the alternative was never notifying at all.
- Machine lookups are cached for `MACHINES_CACHE_TTL_SECONDS` (default 60s), so a
  change to a machine's assigned technician/informed users can take up to that long to
  take effect.
