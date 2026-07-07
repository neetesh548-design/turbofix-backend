# TurboFix Backend — Phase 1+2+3+4+5 (receive, log, transcribe, summarize, fan-out, harden & document vault)

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

## Phase 5 — Document Vault (manuals, diagrams, BOM, consumables)

A separate, authenticated API surface (`app/vault_router.py`, mounted alongside the
anonymous WhatsApp webhook) for the small group of staff — **owner, supervisor,
maintenance_head** — who maintain each machine's manual, circuit/hydraulic diagrams,
spare-parts catalog, BOM, and consumables list. Workers reporting a fault over
WhatsApp never touch this; it's a completely separate login.

- **`POST /auth/login`** — `{identifier, password}` (identifier is phone or email) →
  a JWT (`JWT_SECRET_KEY`/`JWT_EXPIRE_MINUTES` in `.env`, default 8h) carrying the
  user's `company_code` and `role`. Passwords are bcrypt-hashed in the `Users` tab.
- **`GET /vault/machines`** — the caller's company's machines, for populating a picker.
- **Documents** (`GET/POST /vault/documents`, `GET /vault/documents/{id}/download`,
  `DELETE /vault/documents/{id}`) — `category` is one of `manual`, `circuit_diagram`,
  `hydraulic_diagram`, `spare_parts_catalog`, `other`. Upload is `multipart/form-data`
  (`machine_id`, `category`, `title`, `file`); allowed types/size are
  `ALLOWED_DOCUMENT_EXTENSIONS`/`MAX_DOCUMENT_SIZE_MB` in `.env`. File bytes are
  stored via `app/file_storage.py` (`DOCUMENT_STORE=local`, the default, writes under
  `DOCUMENT_STORE_DIR`; `DOCUMENT_STORE=gcs` uploads to a Google Cloud Storage bucket —
  written but never exercised against a real bucket, same status as `store_sheets.py`).
- **Spare parts / BOM** (`GET/POST /vault/spare-parts`, `PATCH`/`DELETE .../{part_id}`)
  and **consumables** (same shape at `/vault/consumables`) — simple per-machine
  inventories (`quantity_on_hand`, `unit`, `reorder_level`, ...).
- **Roles:** `owner` and `maintenance_head` can create/edit/delete; `supervisor` is
  read-only (`app/auth.py`'s `WRITE_ROLES`) — matches how supervisors are described
  elsewhere in the product, as informed users rather than machine owners. Every
  endpoint also enforces the same multi-tenant isolation as tickets/machines: a user
  can only ever see or touch their own `company_code`'s data (a cross-company
  request 404s, not 403s, so company existence isn't leaked either).
- **Creating a login is a deliberate admin action**, not self-serve — there's no
  signup endpoint, only `scripts/create_user.py`:
  ```bash
  .venv/bin/python scripts/create_user.py --company-code ACME3 --name "New Hire" \
    --phone +919800000000 --role maintenance_head
  ```
- **Staff portal UI:** `../demo-site/vault.html` (+ `assets/vault.js`/`vault.css`) is a
  small static page — login form, per-machine document/BOM/consumables browser —
  that calls this API directly from the browser. It's part of the demo-site's static
  build (no server-side rendering), so CORS is enabled on this API
  (`VAULT_CORS_ORIGINS` in `.env`, defaults to `*` since auth here is a Bearer JWT,
  not a cookie, so there's no CSRF/credential-leak exposure from a wide origin list —
  tighten to an explicit allowlist for a real deployment). Point the page's
  "Advanced: backend URL" field at wherever this API actually runs.
- **Sample logins** (seeded by `build_tracker.py`, demo passwords, rotate before real
  data goes in): `rakesh@acmeforge.example` / `AcmeOwner@2026` (ACME3 owner),
  `vikram@acmeforge.example` / `AcmeMaint@2026` (ACME3 maintenance_head),
  `sunil@acmeforge.example` / `AcmeSuper@2026` (ACME3 supervisor, read-only).

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
- The Document Vault's `DOCUMENT_STORE=gcs` path (`app/file_storage.py`) is written but
  has never run against a real Google Cloud Storage bucket — same unverified status as
  `store_sheets.py`. `DOCUMENT_STORE=local` (the default) is what's actually been tested.
- `VAULT_CORS_ORIGINS` defaults to `*` (any origin can call the vault API, given a
  valid bearer token) — fine for a pilot/demo, but worth tightening to the real
  deployed staff-portal origin before this holds real customer documents.
