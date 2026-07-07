"""Phase 5 - Document Vault authentication.

A small, real (not stubbed) JWT auth layer so the webhook backend can also serve a
handful of authenticated endpoints for owner/supervisor/maintenance_head users to
manage machine documents, spare parts (BOM), and consumables. WhatsApp users (the
worker reporting a fault) never authenticate - this is only for the small group of
staff who need to log in and manage documentation.
"""

import time
from enum import Enum
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import config


class Role(str, Enum):
    OWNER = "owner"
    SUPERVISOR = "supervisor"
    MAINTENANCE_HEAD = "maintenance_head"


# Roles allowed to create/modify/delete documents, spare parts, and consumables.
# Supervisors can view everything but not change it - matches how they're described
# elsewhere in the product as "informed users" rather than machine owners.
WRITE_ROLES = {Role.OWNER, Role.MAINTENANCE_HEAD}


def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # malformed hash (e.g. blank/placeholder row in the tracker) - never a match
        return False


def create_access_token(*, user_id: str, company_code: str, role: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "company_code": company_code,
        "role": role,
        "iat": now,
        "exp": now + config.JWT_EXPIRE_MINUTES * 60,
    }
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


_bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser:
    """The authenticated caller's identity, parsed straight from the JWT - no extra
    lookup against the Users tab needed for every request."""

    def __init__(self, user_id: str, company_code: str, role: str):
        self.user_id = user_id
        self.company_code = company_code
        self.role = role

    def can_write(self) -> bool:
        return self.role in {r.value for r in WRITE_ROLES}

    def assert_same_company(self, other_company_code: str) -> None:
        """Enforces the same multi-tenant isolation used for tickets/machines
        elsewhere in the product: a user must never read or write another
        company's documents, spare parts, or consumables."""
        if self.company_code != other_company_code:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    def assert_can_write(self) -> None:
        if not self.can_write():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="only an owner or maintenance head can do this",
            )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired token")

    return CurrentUser(
        user_id=payload["sub"],
        company_code=payload["company_code"],
        role=payload["role"],
    )
