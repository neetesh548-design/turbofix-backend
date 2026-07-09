"""Shared, cached Google Sheets client for all Sheets-backed repositories.

A single gspread.Client is reused across all repo instances in the same
process — creating one per call would exhaust OAuth token limits quickly.
Thread-safe via a simple module-level lock.
"""

import threading
from functools import lru_cache

import gspread
from google.oauth2.service_account import Credentials

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",  # needed by Drive file storage
]

_lock = threading.Lock()
_client: gspread.Client = None


def get_client(service_account_file: str) -> gspread.Client:
    """Return a shared, authenticated gspread client (created once, then cached)."""
    global _client
    with _lock:
        if _client is None:
            creds = Credentials.from_service_account_file(service_account_file, scopes=_SCOPES)
            _client = gspread.authorize(creds)
        return _client


def get_spreadsheet(service_account_file: str, sheet_id: str) -> gspread.Spreadsheet:
    """Return the spreadsheet object for the given sheet ID."""
    return get_client(service_account_file).open_by_key(sheet_id)
