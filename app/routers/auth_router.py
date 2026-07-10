"""Auth router — login, registration, supervisor management, and password reset."""

from datetime import datetime, timezone
from urllib.parse import quote
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, File, Form, UploadFile
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
from app.dependencies import get_users, get_machines
from app.infrastructure.logging import get_logger
from app.repositories.base import UserRepository, MachineRepository, new_document_id
from app.infrastructure.file_storage import get_file_storage

log = get_logger("turbofix.auth")
router = APIRouter(prefix="/auth")


def _company_approved(company: dict) -> bool:
    return str(company.get("approved") or "").strip().lower() in {"yes", "true", "1"}


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

    company = users.get_company(user["company_code"])
    if company is None or not _company_approved(company):
        raise HTTPException(
            status_code=403,
            detail="Your company registration is pending approval by a TurboFix admin.",
        )

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
# Self-service owner + company registration (pending admin approval)
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    company_code: str
    company_name: str
    admin_contact_phone: str
    owner_name: str
    owner_email: str
    owner_password: str


@router.post("/register", status_code=201)
def register_company(body: RegisterRequest, users: UserRepository = Depends(get_users)):
    """Self-service owner registration — creates company (unapproved) + owner account."""
    company_code = body.company_code.strip().upper()
    if len(company_code) < 2:
        raise HTTPException(status_code=400, detail="company code must be at least 2 characters")
    if not body.owner_email.strip():
        raise HTTPException(status_code=400, detail="owner email is required")
    if len(body.owner_password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    if users.get_company(company_code) is not None:
        raise HTTPException(status_code=409, detail="company code already exists")

    if users.get_by_identifier(body.owner_email.strip()):
        raise HTTPException(status_code=409, detail="an account with this email already exists")
    if body.admin_contact_phone.strip() and users.get_by_identifier(body.admin_contact_phone.strip()):
        raise HTTPException(status_code=409, detail="an account with this phone already exists")

    users.add_company(
        company_code=company_code,
        company_name=body.company_name.strip(),
        admin_contact_phone=body.admin_contact_phone.strip(),
        machine_quota=5,
        approved=False,
    )

    user_id = users.next_user_id(company_code)
    users.add({
        "user_id": user_id,
        "company_code": company_code,
        "name": body.owner_name.strip(),
        "phone": body.admin_contact_phone.strip(),
        "email": body.owner_email.strip(),
        "role": Role.OWNER.value,
        "password_hash": hash_password(body.owner_password),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })

    log.info("auth.register", company_code=company_code, owner_user=user_id)
    return {
        "status": "pending_approval",
        "message": "Your company has been registered. A TurboFix admin will review and approve your account.",
    }


# ---------------------------------------------------------------------------
# Owner-only: add supervisor to own company
# ---------------------------------------------------------------------------

class AddSupervisorRequest(BaseModel):
    name: str
    phone: str = ""
    email: str = ""
    password: str


@router.post("/supervisors", status_code=201)
def add_supervisor(
    body: AddSupervisorRequest,
    user: CurrentUser = Depends(get_current_user),
    users: UserRepository = Depends(get_users),
):
    """Owner creates a supervisor account under their own company."""
    user.assert_owner()

    if not body.phone and not body.email:
        raise HTTPException(status_code=400, detail="phone or email is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    if (body.phone and users.get_by_identifier(body.phone)) or (
        body.email and users.get_by_identifier(body.email)
    ):
        raise HTTPException(status_code=409, detail="an account with this phone or email already exists")

    user_id = users.next_user_id(user.company_code)
    users.add({
        "user_id": user_id,
        "company_code": user.company_code,
        "name": body.name,
        "phone": body.phone,
        "email": body.email,
        "role": Role.SUPERVISOR.value,
        "password_hash": hash_password(body.password),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })

    log.info("auth.add_supervisor", user_id=user_id, company=user.company_code, added_by=user.user_id)
    return {
        "user_id": user_id,
        "name": body.name,
        "role": Role.SUPERVISOR.value,
        "company_code": user.company_code,
    }


# ---------------------------------------------------------------------------
# Self-service Company registration (owner + payment screenshot upload)
# ---------------------------------------------------------------------------

@router.post("/register", status_code=201)
async def register(
    company_code: str = Form(...),
    company_name: str = Form(...),
    admin_contact_phone: str = Form(...),
    owner_name: str = Form(...),
    owner_email: str = Form(...),
    owner_password: str = Form(...),
    payment_screenshot: UploadFile = File(...),
    users: UserRepository = Depends(get_users),
):
    comp_code = company_code.strip().upper()
    if len(comp_code) < 2:
        raise HTTPException(status_code=400, detail="Company code must be at least 2 characters")
    if len(owner_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    if users.get_company(comp_code) is not None:
        raise HTTPException(status_code=409, detail="Company code already exists")

    if users.get_by_identifier(owner_email) or (admin_contact_phone and users.get_by_identifier(admin_contact_phone)):
        raise HTTPException(status_code=409, detail="An account with this email or contact phone already exists")

    # Upload payment screenshot
    content = await payment_screenshot.read()
    storage = get_file_storage()
    doc_id = new_document_id()
    storage_path = await storage.save(
        company_code=comp_code,
        machine_id="SYSTEM",
        document_id=doc_id,
        filename=payment_screenshot.filename,
        content=content
    )

    # Save company as pending (approved = "no")
    from datetime import datetime as _dt, timezone as _tz
    registered_now = _dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    users.add_company(
        company_code=comp_code,
        company_name=company_name.strip(),
        admin_contact_phone=admin_contact_phone.strip(),
        machine_quota=5,
        approved=False,
        payment_screenshot=storage_path,
        registered_at=registered_now,
    )

    # Seed owner user
    user_id = users.next_user_id(comp_code)
    users.add({
        "user_id": user_id,
        "company_code": comp_code,
        "name": owner_name.strip(),
        "phone": admin_contact_phone.strip(),
        "email": owner_email.strip(),
        "role": Role.OWNER.value,
        "password_hash": hash_password(owner_password),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })

    log.info("auth.register", company_code=comp_code, owner_id=user_id)
    return {
        "status": "pending_approval",
        "message": "Registration submitted! A TurboFix admin will review and approve your company."
    }


# ---------------------------------------------------------------------------
# Owner-onboarded Supervisors
# ---------------------------------------------------------------------------

class SupervisorOnboardRequest(BaseModel):
    name: str
    phone: str = ""
    email: str = ""
    password: str


@router.post("/supervisors", status_code=201)
def onboard_supervisor(
    body: SupervisorOnboardRequest,
    user: CurrentUser = Depends(get_current_user),
    users: UserRepository = Depends(get_users),
):
    if user.role != Role.OWNER.value:
        raise HTTPException(status_code=403, detail="Only owners can onboard supervisors")

    if not body.phone and not body.email:
        raise HTTPException(status_code=400, detail="Phone or email is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    # Check duplicates
    if (body.phone and users.get_by_identifier(body.phone)) or (
        body.email and users.get_by_identifier(body.email)
    ):
        raise HTTPException(status_code=409, detail="An account with this phone or email already exists")

    user_id = users.next_user_id(user.company_code)
    users.add({
        "user_id": user_id,
        "company_code": user.company_code,
        "name": body.name.strip(),
        "phone": body.phone.strip(),
        "email": body.email.strip(),
        "role": Role.SUPERVISOR.value,
        "password_hash": hash_password(body.password),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })

    log.info("auth.onboard_supervisor", company_code=user.company_code, supervisor_id=user_id)
    return {
        "status": "created",
        "user_id": user_id,
        "name": body.name,
        "role": Role.SUPERVISOR.value,
        "company_code": user.company_code,
    }


class SupervisorUpdateRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None


@router.put("/supervisors/{supervisor_id}", status_code=200)
def update_supervisor(
    supervisor_id: str,
    body: SupervisorUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    users: UserRepository = Depends(get_users),
):
    if user.role != Role.OWNER.value:
        raise HTTPException(status_code=403, detail="Only owners can edit supervisors")

    sup = users.get_by_id(supervisor_id)
    if not sup or sup.get("company_code") != user.company_code or sup.get("role") != Role.SUPERVISOR.value:
        raise HTTPException(status_code=404, detail="Supervisor not found")

    fields = {}
    if body.name is not None:
        fields["name"] = body.name.strip()
    if body.phone is not None:
        fields["phone"] = body.phone.strip()
    if body.email is not None:
        fields["email"] = body.email.strip()
    if body.password is not None:
        if len(body.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
        fields["password_hash"] = hash_password(body.password)

    if not fields:
        raise HTTPException(status_code=400, detail="Nothing to update")

    if body.email and body.email != sup.get("email"):
        if users.get_by_identifier(body.email):
            raise HTTPException(status_code=409, detail="An account with this email already exists")
    if body.phone and body.phone != sup.get("phone"):
        if users.get_by_identifier(body.phone):
            raise HTTPException(status_code=409, detail="An account with this phone already exists")

    users.update_user(supervisor_id, fields)
    return {"status": "updated", "user_id": supervisor_id}


@router.delete("/supervisors/{supervisor_id}", status_code=200)
def delete_supervisor(
    supervisor_id: str,
    user: CurrentUser = Depends(get_current_user),
    users: UserRepository = Depends(get_users),
    machines: MachineRepository = Depends(get_machines),
):
    if user.role != Role.OWNER.value:
        raise HTTPException(status_code=403, detail="Only owners can delete supervisors")

    sup = users.get_by_id(supervisor_id)
    if not sup or sup.get("company_code") != user.company_code or sup.get("role") != Role.SUPERVISOR.value:
        raise HTTPException(status_code=404, detail="Supervisor not found")

    company_machines = machines.get_company_machines(user.company_code)
    for m in company_machines:
        if m.get("supervisor_id") == supervisor_id:
            machines.update_machine(m["machine_id"], {"supervisor_id": ""})

    users.delete_user(supervisor_id)
    log.info("auth.delete_supervisor", company_code=user.company_code, supervisor_id=supervisor_id)
    return {"status": "deleted", "user_id": supervisor_id}


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
