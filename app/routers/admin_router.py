"""Admin router — internal TurboFix-team console for company approval and quota management."""

import secrets
import mimetypes
import jwt

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from typing import Optional

from app import config, email_client
from app.admin_page import ADMIN_HTML
from app.auth import create_admin_token, get_current_admin, Role, hash_password
from app.dependencies import get_machines, get_users
from app.infrastructure.logging import get_logger
from app.repositories.base import MachineRepository, UserRepository
from app.infrastructure.file_storage import get_file_storage

_bearer_scheme = HTTPBearer(auto_error=False)

log = get_logger("turbofix.admin")
router = APIRouter(prefix="/admin")


def _company_quota(company: dict) -> int:
    try:
        return int(str(company.get("machine_quota") or 0).strip())
    except (ValueError, TypeError):
        return 0


def _company_approved(company: dict) -> bool:
    return str(company.get("approved") or "").strip().lower() in {"yes", "true", "1"}


class AdminLoginRequest(BaseModel):
    password: str


@router.post("/login")
def admin_login(body: AdminLoginRequest):
    if not secrets.compare_digest(body.password, config.PLATFORM_ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="incorrect admin password")
    return {"access_token": create_admin_token(), "token_type": "bearer"}


@router.get("/companies")
def admin_list_companies(
    _: bool = Depends(get_current_admin),
    users: UserRepository = Depends(get_users),
    machines: MachineRepository = Depends(get_machines),
):
    out = []
    for c in users.list_companies():
        code = c.get("company_code")
        out.append({
            "company_code": code,
            "company_name": c.get("company_name"),
            "admin_contact_phone": c.get("admin_contact_phone"),
            "onboarded_date": str(c.get("onboarded_date") or ""),
            "machine_quota": _company_quota(c),
            "approved": _company_approved(c),
            "machines_used": len(machines.get_company_machines(code)) if code else 0,
            "has_payment_screenshot": bool(c.get("payment_screenshot")),
        })
    return out


@router.get("/companies/{company_code}/payment-screenshot")
async def get_payment_screenshot(
    company_code: str,
    token: Optional[str] = None,
    users: UserRepository = Depends(get_users),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
):
    auth_token = None
    if credentials:
        auth_token = credentials.credentials
    elif token:
        auth_token = token

    if not auth_token:
        raise HTTPException(status_code=401, detail="missing token")

    from app.auth import _ADMIN_PURPOSE
    try:
        payload = jwt.decode(auth_token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
    except jwt.PyJWTError:
        payload = None

    if payload is None or payload.get("purpose") != _ADMIN_PURPOSE:
        raise HTTPException(status_code=401, detail="admin authentication required")

    company = users.get_company(company_code)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    path = company.get("payment_screenshot")
    if not path:
        raise HTTPException(status_code=404, detail="no payment screenshot uploaded")

    storage = get_file_storage()
    try:
        content = await storage.read(path)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Failed to read file: {exc}")

    filename = path.replace("\\", "/").split("/")[-1]
    media_type = mimetypes.guess_type(filename)[0] or "image/jpeg"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )


class CompanyUpdate(BaseModel):
    machine_quota: Optional[int] = None
    approved: Optional[bool] = None


@router.post("/companies/{company_code}")
def admin_update_company(
    company_code: str,
    body: CompanyUpdate,
    _: bool = Depends(get_current_admin),
    users: UserRepository = Depends(get_users),
    machines: MachineRepository = Depends(get_machines),
):
    company = users.get_company(company_code)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")

    was_approved = _company_approved(company)

    fields = {}
    if body.machine_quota is not None:
        if body.machine_quota < 0:
            raise HTTPException(status_code=400, detail="machine_quota cannot be negative")
        fields["machine_quota"] = body.machine_quota
    if body.approved is not None:
        fields["approved"] = "yes" if body.approved else "no"
    if not fields:
        raise HTTPException(status_code=400, detail="nothing to update")

    users.update_company(company_code, fields)
    company = users.get_company(company_code)
    is_approved = _company_approved(company)

    # Send Welcome Email if company is approved now and wasn't before
    if is_approved and not was_approved:
        company_users = users.get_company_users(company_code)
        owner = next((u for u in company_users if u.get("role") == "owner"), None)
        if owner and owner.get("email"):
            email_client.send_email(
                to=owner["email"],
                subject="Your TurboFix Company Registration Approved!",
                body=(
                    f"Hi {owner.get('name', 'Owner')},\n\n"
                    f"We are excited to inform you that your TurboFix company registration for {company.get('company_name', company_code)} has been approved!\n\n"
                    f"You can now log in to your Document Vault and Dashboard using your credentials.\n\n"
                    f"Login here: http://localhost:8000/vault.html\n\n"
                    f"Best regards,\n"
                    f"The TurboFix Team"
                )
            )

    log.info("admin.company_updated", company_code=company_code, fields=list(fields.keys()))
    return {
        "company_code": company_code,
        "machine_quota": _company_quota(company),
        "approved": _company_approved(company),
        "machines_used": len(machines.get_company_machines(company_code)),
    }


class CompanyOnboardRequest(BaseModel):
    company_code: str
    company_name: str
    admin_contact_phone: str
    owner_name: str
    owner_email: str
    owner_password: str
    machine_quota: int = 5


@router.post("/companies", status_code=201)
def admin_onboard_company(
    body: CompanyOnboardRequest,
    _: bool = Depends(get_current_admin),
    users: UserRepository = Depends(get_users),
):
    # 1. Validate inputs
    company_code = body.company_code.strip().upper()
    if len(company_code) < 2:
        raise HTTPException(status_code=400, detail="invalid company code")
    if len(body.owner_password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    
    # 2. Check duplicate
    try:
        existing_company = users.get_company(company_code)
    except Exception as exc:
        log.error("admin.onboard_company.check_duplicate_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to check duplicate company: {exc}")

    if existing_company is not None:
        raise HTTPException(status_code=409, detail="company code already exists")
        
    # 3. Create Company Record
    try:
        users.add_company(
            company_code=company_code,
            company_name=body.company_name.strip(),
            admin_contact_phone=body.admin_contact_phone.strip(),
            machine_quota=body.machine_quota,
            approved=True
        )
    except Exception as exc:
        log.error("admin.onboard_company.add_company_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to write company row: {exc}")
    
    # 4. Seed Owner Account
    from datetime import datetime, timezone
    try:
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
    except Exception as exc:
        log.error("admin.onboard_company.add_user_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to seed owner user row: {exc}")
    
    log.info("admin.company_onboarded", company_code=company_code, owner_user=user_id)
    return {"status": "created", "company_code": company_code, "owner_user_id": user_id}


@router.get("", response_class=HTMLResponse)
def admin_console():
    """Serve the self-contained admin HTML page."""
    return HTMLResponse(ADMIN_HTML)
