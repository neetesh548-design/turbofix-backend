import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.config import SESSION_TTL_SECONDS


@dataclass
class Session:
    ticket_id: str
    machine_id: str
    created_at: float
    notified: bool = False


class SessionStore:
    """Tracks, per sender phone number, the most recent ticket opened from a text
    message, so a voice note arriving shortly after can be attached to it."""

    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._sessions: Dict[str, Session] = {}

    def open(self, phone: str, ticket_id: str, machine_id: str) -> None:
        self._sessions[phone] = Session(ticket_id=ticket_id, machine_id=machine_id, created_at=time.time())

    def get(self, phone: str) -> Optional[Session]:
        session = self._sessions.get(phone)
        if session is None:
            return None
        if time.time() - session.created_at > self._ttl:
            del self._sessions[phone]
            return None
        return session

    def mark_notified(self, phone: str) -> None:
        """Records that this phone's session has already been fanned out, so a later
        voice note that merely enriches the same ticket doesn't trigger a second,
        duplicate notification to the technician/informed users."""
        session = self._sessions.get(phone)
        if session is not None:
            session.notified = True

    def sweep_expired_unnotified(self) -> List[Tuple[str, Session]]:
        """Removes every expired session (regardless of notified status, so memory
        doesn't grow unbounded) and returns the ones that expired without ever being
        fanned out - e.g. a bare machine-ID text message with no follow-up voice
        note. Callers use this to fire a fallback notification rather than leaving
        those tickets silently un-notified forever."""
        now = time.time()
        expired_unnotified = []
        for phone, session in list(self._sessions.items()):
            if now - session.created_at > self._ttl:
                if not session.notified:
                    expired_unnotified.append((phone, session))
                del self._sessions[phone]
        return expired_unnotified
