"""Dependency Injection factories for all TurboFix repositories.

FastAPI's Depends() system calls these functions to inject the right concrete
repository into each route handler.  The key design principle:

- Route handlers and services depend on the abstract interfaces (base.py).
- The concrete implementation (local Excel vs. Google Sheets) is chosen HERE,
  once, based on TICKET_STORE env var, not scattered across every module.
- Tests can override any dependency via app.dependency_overrides[get_tickets]
  without patching global config.

Usage in a route:
    @router.post("/webhook")
    async def handler(tickets: TicketRepository = Depends(get_tickets)):
        ...

Usage in tests:
    def test_something(client):
        app.dependency_overrides[get_tickets] = lambda: FakeTicketRepo()
        resp = client.post("/webhook", json=payload)
"""

from functools import lru_cache

from app import config
from app.repositories.base import (
    DocumentRepository,
    MachineRepository,
    PartsRepository,
    TicketRepository,
    UserRepository,
)


# ---------------------------------------------------------------------------
# Ticket + Machine (share the same store backend selector)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_tickets() -> TicketRepository:
    """Return the configured TicketRepository implementation (cached singleton)."""
    if config.TICKET_STORE == "sheets":
        from app.repositories.sheets.ticket_repo import SheetsTicketRepository
        return SheetsTicketRepository(config.GOOGLE_SERVICE_ACCOUNT_FILE, config.GOOGLE_SHEET_ID)
    from app.repositories.local.ticket_repo import LocalTicketRepository
    return LocalTicketRepository(config.TRACKER_XLSX_PATH)


@lru_cache(maxsize=1)
def get_machines() -> MachineRepository:
    """Return the configured MachineRepository implementation (cached singleton)."""
    if config.TICKET_STORE == "sheets":
        from app.repositories.sheets.ticket_repo import SheetsMachineRepository
        return SheetsMachineRepository(
            config.GOOGLE_SERVICE_ACCOUNT_FILE,
            config.GOOGLE_SHEET_ID,
            config.MACHINES_CACHE_TTL_SECONDS,
        )
    from app.repositories.local.ticket_repo import LocalMachineRepository
    return LocalMachineRepository(config.TRACKER_XLSX_PATH, config.MACHINES_CACHE_TTL_SECONDS)


# ---------------------------------------------------------------------------
# Users (same backend selector as tickets)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_users() -> UserRepository:
    """Return the configured UserRepository implementation (cached singleton)."""
    if config.TICKET_STORE == "sheets":
        from app.repositories.sheets.user_repo import SheetsUserRepository
        return SheetsUserRepository(config.GOOGLE_SERVICE_ACCOUNT_FILE, config.GOOGLE_SHEET_ID)
    from app.repositories.local.user_repo import LocalUserRepository
    return LocalUserRepository(config.TRACKER_XLSX_PATH)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_documents() -> DocumentRepository:
    """Return the configured DocumentRepository implementation (cached singleton)."""
    if config.TICKET_STORE == "sheets":
        from app.repositories.sheets.document_repo import SheetsDocumentRepository
        return SheetsDocumentRepository(config.GOOGLE_SERVICE_ACCOUNT_FILE, config.GOOGLE_SHEET_ID)
    from app.repositories.local.document_repo import LocalDocumentRepository
    return LocalDocumentRepository(config.TRACKER_XLSX_PATH)


# ---------------------------------------------------------------------------
# Parts (spare parts + consumables)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_parts() -> PartsRepository:
    """Return the configured PartsRepository implementation (cached singleton)."""
    if config.TICKET_STORE == "sheets":
        from app.repositories.sheets.parts_repo import SheetsPartsRepository
        return SheetsPartsRepository(config.GOOGLE_SERVICE_ACCOUNT_FILE, config.GOOGLE_SHEET_ID)
    from app.repositories.local.parts_repo import LocalPartsRepository
    return LocalPartsRepository(config.TRACKER_XLSX_PATH)
