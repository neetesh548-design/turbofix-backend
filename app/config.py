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
# Model names match the ones already priced out in progress.md's cost model.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-nano")
