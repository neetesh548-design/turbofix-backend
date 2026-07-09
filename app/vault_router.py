"""Phase 5 - Document Vault HTTP surface.

Everything WhatsApp-facing (webhook, tickets, fan-out) lives in main.py and is
anonymous by design - a worker reporting a fault never logs in. This router is the
opposite: a small set of authenticated endpoints for the handful of staff
(owner/supervisor/maintenance_head) who maintain machine manuals, circuit/hydraulic
diagrams, spare-parts (BOM), and consumables lists. Mounted onto the same FastAPI app
in main.py so there's still only one process to run/deploy.
"""

import mimetypes
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from app import config, documents_store, email_client, file_storage, parts_store, store, users_store
from app.admin_page import ADMIN_HTML
from app.auth import (
    CurrentUser,
    Role,
    create_access_token,
    create_admin_token,
    create_reset_token,
    decode_reset_token,
    get_current_admin,
    get_current_user,
    hash_password,
    reset_token_matches,
    verify_password,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    identifier: str  # phone or email
    password: str


@router.post("/auth/login")
def login(body: LoginRequest):
    user = users_store.get_user_by_identifier(body.identifier)
    if user is None or not verify_password(body.password, user.get("password_hash", "")):
        # Same error for "no such user" and "wrong password" - don't reveal which.
        raise HTTPException(status_code=401, detail="invalid credentials")

    token = create_access_token(user_id=user["user_id"], company_code=user["company_code"], role=user["role"])
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


class SignupRequest(BaseModel):
    company_code: str
    admin_contact_phone: str
    name: str
    phone: str = ""
    email: str = ""
    password: str


@router.post("/auth/signup", status_code=201)
def signup(body: SignupRequest):
    """Self-service signup - can only ever create a supervisor (read-only) account.
    Owner/maintenance_head logins (full write access) still require an admin running
    scripts/create_user.py. company_code alone isn't a real secret (it's printed in
    plaintext on every QR tag), so this also requires the company's
    admin_contact_phone as a shared secret before creating an account under it."""
    if not body.phone and not body.email:
        raise HTTPException(status_code=400, detail="phone or email is required")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    company = users_store.get_company(body.company_code)
    if company is None or (company.get("admin_contact_phone") or "").strip() != body.admin_contact_phone.strip():
        # Same generic error whether the company doesn't exist or the phone doesn't
        # match - don't reveal which, same "don't leak" philosophy as /auth/login.
        raise HTTPException(status_code=401, detail="company code or admin contact phone is incorrect")

    if (body.phone and users_store.get_user_by_identifier(body.phone)) or (
        body.email and users_store.get_user_by_identifier(body.email)
    ):
        raise HTTPException(status_code=409, detail="an account with this phone or email already exists")

    user_id = users_store.next_user_id(body.company_code)
    users_store.add_user({
        "user_id": user_id,
        "company_code": body.company_code,
        "name": body.name,
        "phone": body.phone,
        "email": body.email,
        "role": Role.SUPERVISOR.value,  # self-signup can only ever create a read-only account
        "password_hash": hash_password(body.password),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    })

    token = create_access_token(user_id=user_id, company_code=body.company_code, role=Role.SUPERVISOR.value)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "user_id": user_id,
            "name": body.name,
            "role": Role.SUPERVISOR.value,
            "company_code": body.company_code,
        },
    }


# ---------------------------------------------------------------------------
# Password reset (email link)
# ---------------------------------------------------------------------------

class ForgotPasswordRequest(BaseModel):
    email: str


@router.post("/auth/forgot-password")
def forgot_password(body: ForgotPasswordRequest, background: BackgroundTasks):
    """Emails a one-time reset link to the address on file. Always returns the same
    generic response whether or not an account exists, so it can't be used to probe
    which emails are registered. The send happens in the background, so response time
    doesn't leak it either."""
    user = users_store.get_user_by_identifier(body.email)
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


@router.post("/auth/reset-password")
def reset_password(body: ResetPasswordRequest):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")

    payload = decode_reset_token(body.token)
    if payload is None:
        raise HTTPException(status_code=400, detail="this reset link is invalid or has expired")

    user = users_store.get_user_by_id(payload["sub"])
    # A pwh mismatch means the link was already used, or the password otherwise changed
    # since it was issued - same generic error as a bad/expired token, no detail leaked.
    if user is None or not reset_token_matches(payload, user.get("password_hash", "")):
        raise HTTPException(status_code=400, detail="this reset link is invalid or has expired")

    users_store.update_password(user["user_id"], hash_password(body.new_password))
    return {"message": "Your password has been reset. You can now sign in with it."}


# ---------------------------------------------------------------------------
# Dashboard — per-company KPIs, server-side computed, JWT-scoped
# ---------------------------------------------------------------------------

def _compute_kpis(company_code: str, company_name: str):
    """Compute live KPIs for a company from tickets and machines."""
    machines = store.get_company_machines(company_code)
    tickets = store.get_company_tickets(company_code)

    # KPIs
    open_tickets = sum(1 for t in tickets if t.get("status") == "Open")
    closed_today = sum(1 for t in tickets
                       if t.get("status") == "Closed"
                       and t.get("closed_at")
                       and datetime.fromisoformat(str(t["closed_at"]).replace("Z", "+00:00")).date() == datetime.now(timezone.utc).date())
    machines_down = sum(1 for m in machines if m.get("has_open_tickets"))
    total_tickets = len(tickets)
    total_machines = len(machines)

    # Avg hours to fix (closed tickets only)
    closed_tickets = [t for t in tickets if t.get("status") == "Closed"]
    if closed_tickets:
        hours_sum = 0
        count = 0
        for t in closed_tickets:
            if t.get("hours_to_fix"):
                try:
                    hours_sum += float(t["hours_to_fix"])
                    count += 1
                except (ValueError, TypeError):
                    pass
        avg_hours = hours_sum / count if count > 0 else 0
    else:
        avg_hours = 0

    # Plant health %
    plant_health = 100 if total_machines == 0 else int((total_machines - machines_down) / total_machines * 100)

    # Recent activity (last 5 tickets, most recent first)
    recent = sorted(
        [{"ticket_id": t.get("ticket_id"), "machine_id": t.get("machine_id"),
          "machine_name": t.get("machine_name"), "status": t.get("status"),
          "urgency": t.get("urgency"), "reported_at": t.get("reported_at")}
         for t in tickets],
        key=lambda x: x.get("reported_at") or "2000-01-01",
        reverse=True
    )[:5]

    # Owner view: open tickets needing action, most urgent first, oldest first
    # within the same urgency (a week-old High ticket should shame someone).
    urgency_rank = {"High": 0, "Medium": 1, "Low": 2}
    needs_attention = sorted(
        [{"machine_name": t.get("machine_name"), "urgency": t.get("urgency") or "",
          "description": t.get("description") or t.get("ai_summary") or "",
          "reported_at": t.get("reported_at")}
         for t in tickets if t.get("status") == "Open"],
        key=lambda x: (urgency_rank.get(x["urgency"], 3), str(x["reported_at"] or "9999")),
    )
    urgent_open = sum(1 for t in needs_attention if t["urgency"] == "High")

    # Owner view: tickets reported per ISO week, last 6 weeks (zero-filled so the
    # chart shows quiet weeks too).
    def _parse_reported(value):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value), fmt)
            except (ValueError, TypeError):
                continue
        return None

    today = datetime.now(timezone.utc).date()
    this_week_start = today - timedelta(days=today.weekday())
    week_starts = [this_week_start - timedelta(weeks=i) for i in range(5, -1, -1)]
    week_counts = {ws: 0 for ws in week_starts}
    for t in tickets:
        parsed = _parse_reported(t.get("reported_at"))
        if parsed is None:
            continue
        ws = parsed.date() - timedelta(days=parsed.weekday())
        if ws in week_counts:
            week_counts[ws] += 1
    weekly_trend = [{"week_start": ws.strftime("%d %b"), "count": week_counts[ws]}
                    for ws in week_starts]

    return {
        "company_code": company_code,
        "company_name": company_name,
        "kpis": {
            "open_tickets": open_tickets,
            "machines_down": machines_down,
            "closed_today": closed_today,
            "total_tickets": total_tickets,
            "avg_hours_to_fix": round(avg_hours, 1),
            "plant_health_pct": plant_health,
            "total_machines": total_machines,
            "urgent_open": urgent_open,
        },
        "recent_activity": recent,
        "needs_attention": needs_attention,
        "weekly_trend": weekly_trend,
    }


@router.get("/vault/dashboard")
def get_dashboard(user: CurrentUser = Depends(get_current_user)):
    """Get live KPI dashboard for the user's company."""
    company = users_store.get_company(user.company_code)
    if not company:
        raise HTTPException(status_code=404, detail="company not found")
    return _compute_kpis(user.company_code, company.get("company_name", ""))


# ---------------------------------------------------------------------------
# Machines (read-only lookup so the UI can populate a per-machine picker)
# ---------------------------------------------------------------------------

@router.get("/vault/machines")
def list_machines(user: CurrentUser = Depends(get_current_user)):
    machines = store.load_machines()
    return [
        {"machine_id": machine_id, **machine}
        for machine_id, machine in machines.items()
        if machine["company_code"] == user.company_code
    ]


class MachineIn(BaseModel):
    machine_name: str
    location: str = ""
    assigned_technician_phone: str
    informed_phone_1: str = ""
    informed_phone_2: str = ""
    informed_phone_3: str = ""


def _company_quota(company: dict) -> int:
    """machine_quota may come back as int, str, or blank (older tracker). Blank/invalid
    means 0 - the account can't onboard until the TurboFix team sets a paid quota."""
    try:
        return int(str(company.get("machine_quota") or 0).strip())
    except (ValueError, TypeError):
        return 0


def _company_approved(company: dict) -> bool:
    return str(company.get("approved") or "").strip().lower() in {"yes", "true", "1"}


def _assert_can_onboard(company_code: str) -> dict:
    """Gate machine onboarding on TurboFix approval + paid machine quota. Returns the
    company record (with current usage) so the caller can echo the plan back."""
    company = users_store.get_company(company_code)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")

    if not _company_approved(company):
        raise HTTPException(
            status_code=403,
            detail="Your company is pending TurboFix approval. "
                   "You'll be able to onboard machines once the TurboFix team activates your account.",
        )

    quota = _company_quota(company)
    used = len(store.get_company_machines(company_code))
    if used >= quota:
        # 402 Payment Required is the semantically correct signal for "over plan".
        raise HTTPException(
            status_code=402,
            detail=f"You've reached your plan's limit of {quota} "
                   f"machine{'s' if quota != 1 else ''}. "
                   f"Please upgrade your subscription to onboard more machines.",
        )
    return {"quota": quota, "used": used}


@router.post("/vault/machines", status_code=201)
def create_machine(body: MachineIn, user: CurrentUser = Depends(get_current_user)):
    """Self-service machine onboarding: caller supplies name/location/technician, we
    generate the machine_id (TF-{companyCode}-M{nnn}) and a ready-to-print wa.me QR
    link, so a non-technical owner/maintenance_head never touches the tracker/Sheet
    directly. Blocked once the company hits its paid machine_quota, or before the
    TurboFix team has approved the account."""
    user.assert_can_write()
    plan = _assert_can_onboard(user.company_code)
    machine_code = store.next_machine_code(user.company_code)
    machine_id = f"TF-{user.company_code}-{machine_code}"
    row = {"machine_id": machine_id, "company_code": user.company_code, **body.model_dump()}
    store.create_machine(row)

    wa_link = None
    if config.WHATSAPP_DISPLAY_NUMBER:
        text = quote(f"Issue with {machine_id}: ")
        wa_link = f"https://wa.me/{config.WHATSAPP_DISPLAY_NUMBER}?text={text}"
    # machines_used is post-create, so the UI can show "4 of 5 used" right away.
    return {**row, "wa_link": wa_link,
            "machine_quota": plan["quota"], "machines_used": plan["used"] + 1}


def _get_machine_in_company(machine_id: str, user: CurrentUser) -> dict:
    machine = store.get_machine(machine_id)
    if machine is None or machine["company_code"] != user.company_code:
        raise HTTPException(status_code=404, detail="machine not found")
    return machine


# ---------------------------------------------------------------------------
# Documents (manuals, circuit/hydraulic diagrams, spare-parts catalogs, ...)
# ---------------------------------------------------------------------------

@router.get("/vault/documents")
def list_documents(machine_id: Optional[str] = None, user: CurrentUser = Depends(get_current_user)):
    return documents_store.list_documents(user.company_code, machine_id)


@router.post("/vault/documents", status_code=201)
async def upload_document(
    machine_id: str = Form(...),
    category: str = Form(...),
    title: str = Form(...),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    user.assert_can_write()
    if category not in documents_store.DOCUMENT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"category must be one of {documents_store.DOCUMENT_CATEGORIES}",
        )
    _get_machine_in_company(machine_id, user)

    content = await file.read()
    try:
        file_storage.validate_upload(file.filename, len(content))
    except file_storage.UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except file_storage.FileTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))

    document_id = documents_store.next_document_id()
    storage_path = file_storage.save_file(user.company_code, machine_id, document_id, file.filename, content)
    row = {
        "document_id": document_id,
        "company_code": user.company_code,
        "machine_id": machine_id,
        "category": category,
        "title": title,
        "file_name": file.filename,
        "storage_path": storage_path,
        "uploaded_by": user.user_id,
        "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }
    documents_store.add_document(row)
    return row


@router.get("/vault/documents/{document_id}/download")
def download_document(document_id: str, user: CurrentUser = Depends(get_current_user)):
    doc = documents_store.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(doc["company_code"])

    content = file_storage.read_file(doc["storage_path"])
    media_type = mimetypes.guess_type(doc["file_name"])[0] or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{doc["file_name"]}"'},
    )


@router.delete("/vault/documents/{document_id}", status_code=204)
def delete_document(document_id: str, user: CurrentUser = Depends(get_current_user)):
    doc = documents_store.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(doc["company_code"])
    user.assert_can_write()

    file_storage.delete_file(doc["storage_path"])
    documents_store.delete_document(document_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Spare parts (BOM) + Consumables - same shape (see app/parts_store.py), so the
# two route groups below are thin and near-identical by design.
# ---------------------------------------------------------------------------

class SparePartIn(BaseModel):
    machine_id: str
    part_name: str
    part_number: str = ""
    quantity_on_hand: float = 0
    unit: str = ""
    reorder_level: float = 0
    supplier: str = ""
    notes: str = ""


class SparePartUpdate(BaseModel):
    part_name: Optional[str] = None
    part_number: Optional[str] = None
    quantity_on_hand: Optional[float] = None
    unit: Optional[str] = None
    reorder_level: Optional[float] = None
    supplier: Optional[str] = None
    notes: Optional[str] = None


class ConsumableIn(BaseModel):
    machine_id: str
    name: str
    quantity_on_hand: float = 0
    unit: str = ""
    reorder_level: float = 0
    notes: str = ""


class ConsumableUpdate(BaseModel):
    name: Optional[str] = None
    quantity_on_hand: Optional[float] = None
    unit: Optional[str] = None
    reorder_level: Optional[float] = None
    notes: Optional[str] = None


@router.get("/vault/spare-parts")
def list_spare_parts(machine_id: Optional[str] = None, user: CurrentUser = Depends(get_current_user)):
    return parts_store.list_items("spare_parts", user.company_code, machine_id)


@router.post("/vault/spare-parts", status_code=201)
def create_spare_part(body: SparePartIn, user: CurrentUser = Depends(get_current_user)):
    user.assert_can_write()
    _get_machine_in_company(body.machine_id, user)
    part_id = parts_store.next_item_id("spare_parts")
    row = {"part_id": part_id, "company_code": user.company_code, **body.model_dump()}
    parts_store.add_item("spare_parts", row)
    return row


@router.patch("/vault/spare-parts/{part_id}")
def update_spare_part(part_id: str, body: SparePartUpdate, user: CurrentUser = Depends(get_current_user)):
    item = parts_store.get_item("spare_parts", part_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(item["company_code"])
    user.assert_can_write()

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    parts_store.update_item("spare_parts", part_id, updates)
    return parts_store.get_item("spare_parts", part_id)


@router.delete("/vault/spare-parts/{part_id}", status_code=204)
def delete_spare_part(part_id: str, user: CurrentUser = Depends(get_current_user)):
    item = parts_store.get_item("spare_parts", part_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(item["company_code"])
    user.assert_can_write()

    parts_store.delete_item("spare_parts", part_id)
    return Response(status_code=204)


@router.get("/vault/consumables")
def list_consumables(machine_id: Optional[str] = None, user: CurrentUser = Depends(get_current_user)):
    return parts_store.list_items("consumables", user.company_code, machine_id)


@router.post("/vault/consumables", status_code=201)
def create_consumable(body: ConsumableIn, user: CurrentUser = Depends(get_current_user)):
    user.assert_can_write()
    _get_machine_in_company(body.machine_id, user)
    consumable_id = parts_store.next_item_id("consumables")
    row = {"consumable_id": consumable_id, "company_code": user.company_code, **body.model_dump()}
    parts_store.add_item("consumables", row)
    return row


@router.patch("/vault/consumables/{consumable_id}")
def update_consumable(consumable_id: str, body: ConsumableUpdate, user: CurrentUser = Depends(get_current_user)):
    item = parts_store.get_item("consumables", consumable_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(item["company_code"])
    user.assert_can_write()

    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    parts_store.update_item("consumables", consumable_id, updates)
    return parts_store.get_item("consumables", consumable_id)


@router.delete("/vault/consumables/{consumable_id}", status_code=204)
def delete_consumable(consumable_id: str, user: CurrentUser = Depends(get_current_user)):
    item = parts_store.get_item("consumables", consumable_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(item["company_code"])
    user.assert_can_write()

    parts_store.delete_item("consumables", consumable_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Internal admin console (TurboFix team) — approve companies, set machine quota
# ---------------------------------------------------------------------------

class AdminLoginRequest(BaseModel):
    password: str


@router.post("/admin/login")
def admin_login(body: AdminLoginRequest):
    # Constant-time compare so a wrong password can't be teased out by timing.
    if not secrets.compare_digest(body.password, config.PLATFORM_ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="incorrect admin password")
    return {"access_token": create_admin_token(), "token_type": "bearer"}


@router.get("/admin/companies")
def admin_list_companies(_: bool = Depends(get_current_admin)):
    """Every company with its plan and current usage, for the admin table."""
    out = []
    for c in users_store.list_companies():
        code = c.get("company_code")
        out.append({
            "company_code": code,
            "company_name": c.get("company_name"),
            "admin_contact_phone": c.get("admin_contact_phone"),
            "onboarded_date": str(c.get("onboarded_date") or ""),
            "machine_quota": _company_quota(c),
            "approved": _company_approved(c),
            "machines_used": len(store.get_company_machines(code)) if code else 0,
        })
    return out


class CompanyUpdate(BaseModel):
    machine_quota: Optional[int] = None
    approved: Optional[bool] = None


@router.post("/admin/companies/{company_code}")
def admin_update_company(company_code: str, body: CompanyUpdate, _: bool = Depends(get_current_admin)):
    """Approve/unapprove a company and/or change its paid machine quota."""
    if users_store.get_company(company_code) is None:
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

    users_store.update_company(company_code, fields)
    company = users_store.get_company(company_code)
    return {
        "company_code": company_code,
        "machine_quota": _company_quota(company),
        "approved": _company_approved(company),
        "machines_used": len(store.get_company_machines(company_code)),
    }


@router.get("/admin", response_class=HTMLResponse)
def admin_console():
    """The internal TurboFix-team admin page. Self-contained HTML served straight from
    the backend - no build step, no separate host. Auth happens client-side against
    /admin/login; every data call carries the admin bearer token."""
    return HTMLResponse(ADMIN_HTML)
