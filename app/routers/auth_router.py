"""Auth router — login, signup, and password reset endpoints.

Thin HTTP adapter: validates inputs, calls auth/user service logic, returns responses.
All token creation and password hashing lives in app/auth.py (unchanged).
"""

from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app import config, email_client
from app.auth import (
    CurrentUser,
    Role,
    create_access_token,
    create_reset_token,
    decode_reset_token,
    get_current_user,
    hash_password,
    reset_token_matches,
    verify_password,
)
from app.dependencies import get_users
from app.infrastructure.logging import get_logger
from app.repositories.base import UserRepository

log = get_logger("turbofix.auth")
router = APIRouter(prefix="/auth")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    identifier: str  # phone or email
    password: str


@router.post("/login")
def login(body: LoginRequest, users: UserRepository = Depends(get_users)):
    user = users.get_by_identifier(body.identifier)
    if user is None or not verify_password(body.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = create_access_token(
        user_id=user["user_id"], company_code=user["company_code"], role=user["role"]
    )
    log.info("auth.login", user_id=user["user_id"], company=user["company_code"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "user_id": user["user_id"],
            "name": user["name"],
            "role": user["role"],
            "company_code": user["company_code"],
        },
    }


# ---------------------------------------------------------------------------
# Self-service signup (supervisor / read-only accounts only)
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    company_code: str
    admin_contact_phone: str
    name: str
    phone: str = ""
    email: str = ""
    password: str


@router.post("/signup", status_code=201)
def signup(body: SignupRequest, users: UserRepository = Depends(get_users)):
    """Self-service signup — can only ever create a supervisor (read-only) account."""
    if not body.phone and not body.email:
        raise HTTPException(status_code=400, detail="phone or email is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    company = users.get_company(body.company_code)
    stored_phone = "".join(c for c in str(company.get("admin_contact_phone", "")) if c.isdigit()) if company else ""
    input_phone = "".join(c for c in body.admin_contact_phone if c.isdigit())
    if company is None or stored_phone != input_phone:
        raise HTTPException(status_code=401, detail="company code or admin contact phone is incorrect")

    if (body.phone and users.get_by_identifier(body.phone)) or (
        body.email and users.get_by_identifier(body.email)
    ):
        raise HTTPException(status_code=409, detail="an account with this phone or email already exists")

    user_id = users.next_user_id(body.company_code)
    users.add({
        "user_id": user_id,
        "company_code": body.company_code,
        "name": body.name,
        "phone": body.phone,
        "email": body.email,
        "role": Role.SUPERVISOR.value,
        "password_hash": hash_password(body.password),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })

    token = create_access_token(
        user_id=user_id, company_code=body.company_code, role=Role.SUPERVISOR.value
    )
    log.info("auth.signup", user_id=user_id, company=body.company_code)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {"user_id": user_id, "name": body.name, "role": Role.SUPERVISOR.value,
                 "company_code": body.company_code},
    }


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

class ForgotPasswordRequest(BaseModel):
    email: str


@router.post("/forgot-password")
def forgot_password(
    body: ForgotPasswordRequest,
    background: BackgroundTasks,
    users: UserRepository = Depends(get_users),
):
    """Send a one-time reset link. Always returns the same generic response."""
    user = users.get_by_identifier(body.email)
    if user and user.get("email"):
        token = create_reset_token(user_id=user["user_id"], password_hash=user.get("password_hash", ""))
        link = f"{config.RESET_LINK_BASE}?token={quote(token)}"
        mins = config.PASSWORD_RESET_EXPIRE_MINUTES
        text = (
            f"Hi {user.get('name') or 'there'},\n\n"
            f"Someone asked to reset the password for your TurboFix account.\n"
            f"If it was you, open this link within {mins} minutes to choose a new password:\n\n"
            f"{link}\n\n"
            f"If it wasn't you, ignore this email - your password stays unchanged.\n"
        )
        background.add_task(email_client.send_email, user["email"], "Reset your TurboFix password", text)
    return {"message": "If an account with that email exists, a reset link has been sent."}


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, users: UserRepository = Depends(get_users)):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    payload = decode_reset_token(body.token)
    if payload is None:
        raise HTTPException(status_code=400, detail="this reset link is invalid or has expired")

    user = users.get_by_id(payload["sub"])
    if user is None or not reset_token_matches(payload, user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="this reset link is invalid or has expired")

    users.update_password(user["user_id"], hash_password(body.new_password))
    log.info("auth.password_reset", user_id=user["user_id"])
    return {"message": "Your password has been reset. You can now sign in with it."}
