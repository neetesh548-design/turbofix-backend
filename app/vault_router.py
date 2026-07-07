"""Phase 5 - Document Vault HTTP surface.

Everything WhatsApp-facing (webhook, tickets, fan-out) lives in main.py and is
anonymous by design - a worker reporting a fault never logs in. This router is the
opposite: a small set of authenticated endpoints for the handful of staff
(owner/supervisor/maintenance_head) who maintain machine manuals, circuit/hydraulic
diagrams, spare-parts (BOM), and consumables lists. Mounted onto the same FastAPI app
in main.py so there's still only one process to run/deploy.
"""

import mimetypes
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app import config, documents_store, file_storage, parts_store, store, users_store
from app.auth import CurrentUser, Role, create_access_token, get_current_user, hash_password, verify_password

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
        },
        "recent_activity": recent,
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


@router.post("/vault/machines", status_code=201)
def create_machine(body: MachineIn, user: CurrentUser = Depends(get_current_user)):
    """Self-service machine onboarding: caller supplies name/location/technician, we
    generate the machine_id (TF-{companyCode}-M{nnn}) and a ready-to-print wa.me QR
    link, so a non-technical owner/maintenance_head never touches the tracker/Sheet
    directly."""
    user.assert_can_write()
    machine_code = store.next_machine_code(user.company_code)
    machine_id = f"TF-{user.company_code}-{machine_code}"
    row = {"machine_id": machine_id, "company_code": user.company_code, **body.model_dump()}
    store.create_machine(row)

    wa_link = None
    if config.WHATSAPP_DISPLAY_NUMBER:
        text = quote(f"Issue with {machine_id}: ")
        wa_link = f"https://wa.me/{config.WHATSAPP_DISPLAY_NUMBER}?text={text}"
    return {**row, "wa_link": wa_link}


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
