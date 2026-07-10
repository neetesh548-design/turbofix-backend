"""Admin router — internal TurboFix-team console for company approval and quota management."""

import secrets

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

from app import config
from app.admin_page import ADMIN_HTML
from app.auth import create_admin_token, get_current_admin, Role, hash_password
from app.dependencies import get_machines, get_users
from app.infrastructure.logging import get_logger
from app.repositories.base import MachineRepository, UserRepository

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
        })
    return out


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
    if users.get_company(company_code) is None:
        raise HTTPException(status_code=404, detail="company not found")

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
