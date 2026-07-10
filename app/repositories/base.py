"""Abstract base classes (interfaces) for all TurboFix data repositories.

Every concrete repository (local Excel or Google Sheets) must implement the
abstract methods declared here.  This gives us:

- Open/Closed: add a new storage backend by adding a new class, not editing old ones.
- Liskov Substitution: any concrete repo can replace another transparently.
- Interface Segregation: each entity has its own focused interface.
- Dependency Inversion: services depend on these abstractions, not concrete classes.
"""

import secrets
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Shared helpers that are pure logic (not I/O), so they live here once.
# ---------------------------------------------------------------------------

def new_ticket_id() -> str:
    return f"T{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


def new_user_id(company_code: str) -> str:
    return f"U-{company_code}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


def new_document_id() -> str:
    return f"DOC-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


def new_item_id(kind: str) -> str:
    prefix = {"spare_parts": "SP", "consumables": "CON"}[kind]
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


def new_event_id() -> str:
    return f"EVT-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


def new_kpi_id() -> str:
    return f"KPI-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


def new_kpi_entry_id() -> str:
    return f"KD-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


# Column schemas — shared constants so local/sheets repos never drift apart.
MACHINES_HEADER = [
    "machine_id", "company_code", "machine_name", "location",
    "assigned_technician_phone", "informed_phone_1", "informed_phone_2",
    "informed_phone_3", "supervisor_id", "has_open_tickets", "last_activity_at",
]

TICKETS_HEADER = [
    "ticket_id", "machine_id", "company_code", "machine_name", "reported_at",
    "reporter_phone", "description", "ai_summary", "urgency", "status",
    "closed_at", "hours_to_fix", "voice_note_media_id", "photo_media_id",
    "language", "closed_by",
]

MACHINE_EVENTS_HEADER = [
    "event_id", "machine_id", "company_code", "ticket_id", "event_type",
    "timestamp", "actor_phone", "description", "media_type", "media_id",
    "language",
]

USERS_HEADER = [
    "user_id", "company_code", "name", "phone", "email",
    "role", "password_hash", "created_at",
]

COMPANIES_HEADER = [
    "company_code", "company_name", "admin_contact_phone", "onboarded_date",
    "machine_quota", "approved", "payment_screenshot", "registered_at",
]

DOCUMENTS_HEADER = [
    "document_id", "company_code", "machine_id", "category", "title",
    "file_name", "storage_path", "uploaded_by", "uploaded_at",
]

DOCUMENT_CATEGORIES = [
    "manual", "circuit_diagram", "hydraulic_diagram", "spare_parts_catalog", "other",
]

SPARE_PARTS_HEADER = [
    "part_id", "company_code", "machine_id", "part_name", "part_number",
    "quantity_on_hand", "unit", "reorder_level", "supplier", "notes",
]

CONSUMABLES_HEADER = [
    "consumable_id", "company_code", "machine_id", "name",
    "quantity_on_hand", "unit", "reorder_level", "notes",
]

CUSTOM_KPIS_HEADER = [
    "kpi_id", "company_code", "kpi_name", "kpi_type", "unit",
    "target_value", "warning_threshold", "critical_threshold",
    "cost_per_hour", "display_order", "created_at",
]

KPI_DATA_HEADER = [
    "entry_id", "company_code", "kpi_id", "value",
    "recorded_at", "recorded_by",
]


# ---------------------------------------------------------------------------
# Abstract repository interfaces
# ---------------------------------------------------------------------------

class TicketRepository(ABC):
    """Read/write access to the Tickets data entity."""

    @abstractmethod
    def next_ticket_id(self) -> str:
        """Generate a new unique ticket ID."""

    @abstractmethod
    def append(self, row: dict) -> None:
        """Append a new ticket row. Keys must match TICKETS_HEADER."""

    @abstractmethod
    def get(self, ticket_id: str) -> Optional[dict]:
        """Return the ticket dict for ticket_id, or None if not found."""

    @abstractmethod
    def attach_voice_note(self, ticket_id: str, media_id: str) -> bool:
        """Set voice_note_media_id on the matching row. Returns True if found."""

    @abstractmethod
    def update_ai_fields(
        self,
        ticket_id: str,
        ai_summary: str,
        urgency: str,
        description: Optional[str] = None,
    ) -> bool:
        """Update AI-generated fields on the matching ticket. Returns True if found."""

    @abstractmethod
    def get_company_tickets(self, company_code: str) -> List[dict]:
        """Return all tickets belonging to a company."""

    @abstractmethod
    def attach_photo(self, ticket_id: str, media_id: str) -> bool:
        """Set photo_media_id on the matching row. Returns True if found."""

    @abstractmethod
    def update_language(self, ticket_id: str, language: str) -> bool:
        """Set the detected language on the matching ticket. Returns True if found."""

    @abstractmethod
    def close_ticket(self, ticket_id: str, closed_by: str) -> bool:
        """Mark a ticket as Closed with timestamp and who closed it. Returns True if found."""

    @abstractmethod
    def find_by_id_prefix(self, prefix: str) -> Optional[dict]:
        """Find a ticket whose ticket_id starts with prefix (case-insensitive)."""


class EventRepository(ABC):
    """Read/write access to the MachineEvents data entity."""

    @abstractmethod
    def append(self, row: dict) -> None:
        """Append a new event row. Keys must match MACHINE_EVENTS_HEADER."""

    @abstractmethod
    def get_machine_events(self, machine_id: str) -> List[dict]:
        """Return all events for a machine, oldest first."""

    @abstractmethod
    def get_company_events(self, company_code: str) -> List[dict]:
        """Return all events for a company."""


class MachineRepository(ABC):
    """Read/write access to the Machines data entity."""

    @abstractmethod
    def load(self) -> Dict[str, dict]:
        """Return {machine_id: {...}} for all machines (may be cached)."""

    @abstractmethod
    def get(self, machine_id: str) -> Optional[dict]:
        """Return the machine dict, or None if not found."""

    @abstractmethod
    def create(self, row: dict) -> None:
        """Append a new machine row. Keys must match MACHINES_HEADER (minus has_open_tickets)."""

    @abstractmethod
    def invalidate_cache(self) -> None:
        """Force the next load() to re-read from the backing store."""

    @abstractmethod
    def next_machine_code(self, company_code: str) -> str:
        """Return the next Mnnn code for a company, e.g. 'M003'."""

    @abstractmethod
    def get_company_machines(self, company_code: str) -> List[dict]:
        """Return all machines belonging to a company."""

    @abstractmethod
    def update_machine(self, machine_id: str, fields: dict) -> bool:
        """Patch fields on a machine. Returns True if found."""


class UserRepository(ABC):
    """Read/write access to the Users and Companies entities."""

    @abstractmethod
    def next_user_id(self, company_code: str) -> str:
        """Generate a new unique user ID scoped to a company."""

    @abstractmethod
    def get_by_identifier(self, identifier: str) -> Optional[dict]:
        """Look up a user by phone or email (case-insensitive)."""

    @abstractmethod
    def get_by_id(self, user_id: str) -> Optional[dict]:
        """Return the user dict for user_id, or None."""

    @abstractmethod
    def add(self, row: dict) -> None:
        """Append a new user row. Keys must match USERS_HEADER."""

    @abstractmethod
    def update_password(self, user_id: str, new_password_hash: str) -> bool:
        """Overwrite password_hash for one user. Returns True if found."""

    @abstractmethod
    def update_user(self, user_id: str, fields: dict) -> bool:
        """Patch user fields (name, email, phone, password_hash, role) in Users sheet. Returns True if found."""

    @abstractmethod
    def delete_user(self, user_id: str) -> bool:
        """Delete user row by user_id. Returns True if deleted."""

    @abstractmethod
    def get_company(self, company_code: str) -> Optional[dict]:
        """Return the company dict for company_code, or None."""

    @abstractmethod
    def list_companies(self) -> List[dict]:
        """Return all companies (for the admin console)."""

    @abstractmethod
    def update_company(self, company_code: str, fields: dict) -> bool:
        """Patch fields (machine_quota, approved, …) for one company. Returns True if found."""

    @abstractmethod
    def add_company(self, company_code: str, company_name: str, admin_contact_phone: str, machine_quota: int, approved: bool, payment_screenshot: str = "", registered_at: str = "") -> None:
        """Insert a new company row. Keys must match COMPANIES_HEADER."""

    @abstractmethod
    def get_company_users(self, company_code: str) -> List[dict]:
        """Return all users belonging to a company."""


class DocumentRepository(ABC):
    """Read/write access to the Documents metadata entity."""

    @abstractmethod
    def next_document_id(self) -> str:
        """Generate a new unique document ID."""

    @abstractmethod
    def list(self, company_code: str, machine_id: Optional[str] = None) -> List[dict]:
        """Return all documents for a company (optionally filtered by machine)."""

    @abstractmethod
    def get(self, document_id: str) -> Optional[dict]:
        """Return the document dict, or None."""

    @abstractmethod
    def add(self, row: dict) -> None:
        """Append a new document row. Keys must match DOCUMENTS_HEADER."""

    @abstractmethod
    def delete(self, document_id: str) -> bool:
        """Remove the matching document row. Returns True if found."""


class PartsRepository(ABC):
    """Read/write access to the SpareParts and Consumables entities.

    Both share the same CRUD shape (kind distinguishes them), which is
    why they live in a single interface rather than two.
    """

    @abstractmethod
    def next_item_id(self, kind: str) -> str:
        """Generate a new unique item ID for 'spare_parts' or 'consumables'."""

    @abstractmethod
    def list_items(
        self, kind: str, company_code: str, machine_id: Optional[str] = None
    ) -> List[dict]:
        """Return all items of `kind` for a company (optionally filtered by machine)."""

    @abstractmethod
    def get_item(self, kind: str, item_id: str) -> Optional[dict]:
        """Return the item dict, or None."""

    @abstractmethod
    def add_item(self, kind: str, row: dict) -> None:
        """Append a new item row."""

    @abstractmethod
    def update_item(self, kind: str, item_id: str, updates: dict) -> bool:
        """Patch fields on the matching item. Returns True if found."""

    @abstractmethod
    def delete_item(self, kind: str, item_id: str) -> bool:
        """Remove the matching item row. Returns True if found."""


class CustomKpiRepository(ABC):
    """Read/write access to owner-defined custom KPI configs and daily data entries."""

    @abstractmethod
    def list_kpis(self, company_code: str) -> List[dict]:
        """Return all custom KPI configs for a company."""

    @abstractmethod
    def get_kpi(self, kpi_id: str) -> Optional[dict]:
        """Return a single KPI config, or None."""

    @abstractmethod
    def add_kpi(self, row: dict) -> None:
        """Append a new custom KPI config row."""

    @abstractmethod
    def update_kpi(self, kpi_id: str, updates: dict) -> bool:
        """Patch fields on a KPI config. Returns True if found."""

    @abstractmethod
    def delete_kpi(self, kpi_id: str) -> bool:
        """Remove a custom KPI config. Returns True if found."""

    @abstractmethod
    def list_data(self, company_code: str, kpi_id: Optional[str] = None, limit: int = 30) -> List[dict]:
        """Return recent data entries for a company, optionally filtered by kpi_id."""

    @abstractmethod
    def add_data(self, row: dict) -> None:
        """Append a new KPI data entry row."""
