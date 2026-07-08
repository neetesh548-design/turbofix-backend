import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parent.parent

# "local" writes tickets straight into TurboFix-Tracker.xlsx (no credentials needed,
# used for dev/testing). "sheets" writes to a live Google Sheet via a service account
# (what production should use).
TICKET_STORE = os.getenv("TICKET_STORE", "local")

TRACKER_XLSX_PATH = os.getenv(
    "TRACKER_XLSX_PATH", str(BACKEND_DIR.parent / "TurboFix-Tracker.xlsx")
)

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")

WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v20.0")
# The Cloud API "from" number fan-out sends as (Meta phone_number_id, not the raw number).
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

# The human-dialable TurboFix WhatsApp number (no "+", no spaces, e.g. "919900012345") -
# distinct from WHATSAPP_PHONE_NUMBER_ID above. Used to build the wa.me QR link returned
# by POST /vault/machines. Blank by default; the vault UI degrades gracefully without it.
WHATSAPP_DISPLAY_NUMBER = os.getenv("WHATSAPP_DISPLAY_NUMBER", "")

# Phase 3 fan-out: technician/informed users haven't messaged TurboFix themselves, so
# Meta requires a pre-approved message template (not free-form text) to reach them
# outside the 24h customer service window. This name/language must match a template
# already approved in Meta Business Manager.
WHATSAPP_TICKET_TEMPLATE_NAME = os.getenv("WHATSAPP_TICKET_TEMPLATE_NAME", "turbofix_new_ticket")
WHATSAPP_TICKET_TEMPLATE_LANGUAGE = os.getenv("WHATSAPP_TICKET_TEMPLATE_LANGUAGE", "en_US")

MEDIA_STORE_DIR = Path(os.getenv("MEDIA_STORE_DIR", str(BACKEND_DIR / "media_store")))
MEDIA_STORE_DIR.mkdir(parents=True, exist_ok=True)

# how long a text message's machine-ID context stays "open" waiting for a
# follow-up voice note from the same sender
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "900"))

# Harden phase: how often the background sweep checks for sessions that expired
# without ever being fanned out (e.g. a bare machine-ID text with no follow-up voice
# note) and fires a fallback notification for them instead of leaving them silent.
SESSION_SWEEP_INTERVAL_SECONDS = int(os.getenv("SESSION_SWEEP_INTERVAL_SECONDS", "60"))

# Harden phase: how long a Machines-tab read is cached before re-reading the
# tracker/Sheet. Machine registration is rare compared to message volume, so this
# avoids a full re-read on every single incoming message.
MACHINES_CACHE_TTL_SECONDS = int(os.getenv("MACHINES_CACHE_TTL_SECONDS", "60"))

# AI layer (Phase 2): transcription + structured-brief summarization.
# Two interchangeable providers:
#   - "gemini" — Google Gemini, which handles both audio transcription and JSON
#     summarization natively and has a free tier (the demo/pilot default: zero cost).
#   - "openai" — the original paid path, models priced out in progress.md's cost model.
# "auto" (default) picks Gemini if GEMINI_API_KEY is set, else OpenAI if
# OPENAI_API_KEY is set, else the AI layer is skipped entirely (tickets still log).
AI_PROVIDER = os.getenv("AI_PROVIDER", "auto")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-nano")

# Phase 5 - Document Vault (manuals/diagrams/BOM/consumables) with role-based access
# for owner/supervisor/maintenance_head. Dev default is an obviously-insecure secret
# so a real deployment is forced to set its own via the environment.
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-insecure-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "480"))  # a work-shift default

# "local" (default) saves uploaded files to disk under DOCUMENT_STORE_DIR - no
# credentials needed. "gcs" uploads to a Google Cloud Storage bucket via the same
# service-account file already used for GOOGLE_SERVICE_ACCOUNT_FILE above.
DOCUMENT_STORE = os.getenv("DOCUMENT_STORE", "local")
DOCUMENT_STORE_DIR = Path(os.getenv("DOCUMENT_STORE_DIR", str(BACKEND_DIR / "document_store")))
DOCUMENT_STORE_DIR.mkdir(parents=True, exist_ok=True)
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")

MAX_DOCUMENT_SIZE_MB = int(os.getenv("MAX_DOCUMENT_SIZE_MB", "25"))
ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".dwg", ".dxf", ".xlsx", ".csv"}

# Origins allowed to call the vault API from a browser (the demo-site vault.html
# staff portal runs on a different origin than this backend, often on a
# random/auto-picked local dev port). Auth here is a Bearer JWT, not a cookie, so a
# wildcard origin doesn't carry the usual CSRF/credential-leak risk - tighten this to
# a comma-separated allowlist (e.g. the deployed GitHub Pages URL) for production.
VAULT_CORS_ORIGINS = [
    o.strip() for o in os.getenv("VAULT_CORS_ORIGINS", "*").split(",") if o.strip()
]
