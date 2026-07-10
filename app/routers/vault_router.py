"""Vault router — machines, documents, spare parts, and consumables.

Thin HTTP adapter; all business logic lives in services/vault_service.py.
"""

import mimetypes
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app import config
from app.auth import CurrentUser, get_current_user
from app.dependencies import get_documents, get_machines, get_parts
from app.infrastructure.file_storage import FileStorage, get_file_storage
from app.repositories.base import DocumentRepository, MachineRepository, PartsRepository
from app.services import vault_service
from app.infrastructure.logging import get_logger

log = get_logger("turbofix.vault_router")
router = APIRouter(prefix="/vault")


# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------

@router.get("/machines")
def list_machines(
    user: CurrentUser = Depends(get_current_user),
    machines: MachineRepository = Depends(get_machines),
):
    from urllib.parse import quote
    all_machines = machines.load()
    out = []
    for machine_id, machine in all_machines.items():
        if machine["company_code"] == user.company_code:
            wa_link = None
            if config.WHATSAPP_DISPLAY_NUMBER:
                text = quote(f"Issue with {machine_id}: ")
                wa_link = f"https://wa.me/{config.WHATSAPP_DISPLAY_NUMBER}?text={text}"
            out.append({"machine_id": machine_id, "wa_link": wa_link, **machine})
    return out


class MachineIn(BaseModel):
    machine_name: str
    location: str = ""
    assigned_technician_phone: str
    informed_phone_1: str = ""
    informed_phone_2: str = ""
    informed_phone_3: str = ""


def _company_quota(company: dict) -> int:
    try:
        return int(str(company.get("machine_quota") or 0).strip())
    except (ValueError, TypeError):
        return 0


def _company_approved(company: dict) -> bool:
    return str(company.get("approved") or "").strip().lower() in {"yes", "true", "1"}


@router.post("/machines", status_code=201)
def create_machine(
    body: MachineIn,
    user: CurrentUser = Depends(get_current_user),
    machines: MachineRepository = Depends(get_machines),
):
    """Self-service machine onboarding — generates TF-{company}-Mnnn ID."""
    from app.dependencies import get_users

    user.assert_can_write()

    users_repo = get_users()
    company = users_repo.get_company(user.company_code)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")

    if not _company_approved(company):
        raise HTTPException(
            status_code=403,
            detail="Your company is pending TurboFix approval.",
        )

    quota = _company_quota(company)
    used = len(machines.get_company_machines(user.company_code))
    if used >= quota:
        raise HTTPException(
            status_code=402,
            detail=f"You've reached your plan's limit of {quota} machine(s). "
                   "Please upgrade your subscription to onboard more machines.",
        )

    machine_code = machines.next_machine_code(user.company_code)
    machine_id = f"TF-{user.company_code}-{machine_code}"
    row = {"machine_id": machine_id, "company_code": user.company_code, **body.model_dump()}
    machines.create(row)

    wa_link = None
    if config.WHATSAPP_DISPLAY_NUMBER:
        text = quote(f"Issue with {machine_id}: ")
        wa_link = f"https://wa.me/{config.WHATSAPP_DISPLAY_NUMBER}?text={text}"

    return {**row, "wa_link": wa_link, "machine_quota": quota, "machines_used": used + 1}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@router.get("/documents")
def list_documents(
    machine_id: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
    documents: DocumentRepository = Depends(get_documents),
):
    return documents.list(user.company_code, machine_id)


@router.post("/documents", status_code=201)
async def upload_document(
    machine_id: str = Form(...),
    category: str = Form(...),
    title: str = Form(...),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
    machines: MachineRepository = Depends(get_machines),
    documents: DocumentRepository = Depends(get_documents),
):
    content = await file.read()
    storage = get_file_storage()
    return await vault_service.upload_document(
        user=user, machine_id=machine_id, category=category, title=title,
        filename=file.filename, content=content,
        machines=machines, documents=documents, storage=storage,
    )


@router.get("/documents/{document_id}/download")
async def download_document(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    documents: DocumentRepository = Depends(get_documents),
):
    storage = get_file_storage()
    content, filename = await vault_service.download_document(
        document_id=document_id, user=user, documents=documents, storage=storage,
    )
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    user: CurrentUser = Depends(get_current_user),
    documents: DocumentRepository = Depends(get_documents),
):
    storage = get_file_storage()
    await vault_service.delete_document(
        document_id=document_id, user=user, documents=documents, storage=storage,
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Spare parts
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


@router.get("/spare-parts")
def list_spare_parts(
    machine_id: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
    parts: PartsRepository = Depends(get_parts),
):
    return parts.list_items("spare_parts", user.company_code, machine_id)


@router.post("/spare-parts", status_code=201)
def create_spare_part(
    body: SparePartIn,
    user: CurrentUser = Depends(get_current_user),
    machines: MachineRepository = Depends(get_machines),
    parts: PartsRepository = Depends(get_parts),
):
    user.assert_can_write()
    machine = machines.get(body.machine_id)
    if machine is None or machine["company_code"] != user.company_code:
        raise HTTPException(status_code=404, detail="machine not found")
    part_id = parts.next_item_id("spare_parts")
    row = {"part_id": part_id, "company_code": user.company_code, **body.model_dump()}
    parts.add_item("spare_parts", row)
    return row


@router.patch("/spare-parts/{part_id}")
def update_spare_part(
    part_id: str,
    body: SparePartUpdate,
    user: CurrentUser = Depends(get_current_user),
    parts: PartsRepository = Depends(get_parts),
):
    item = parts.get_item("spare_parts", part_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(item["company_code"])
    user.assert_can_write()
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    parts.update_item("spare_parts", part_id, updates)
    return parts.get_item("spare_parts", part_id)


@router.delete("/spare-parts/{part_id}", status_code=204)
def delete_spare_part(
    part_id: str,
    user: CurrentUser = Depends(get_current_user),
    parts: PartsRepository = Depends(get_parts),
):
    item = parts.get_item("spare_parts", part_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(item["company_code"])
    user.assert_can_write()
    parts.delete_item("spare_parts", part_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Consumables
# ---------------------------------------------------------------------------

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


@router.get("/consumables")
def list_consumables(
    machine_id: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
    parts: PartsRepository = Depends(get_parts),
):
    return parts.list_items("consumables", user.company_code, machine_id)


@router.post("/consumables", status_code=201)
def create_consumable(
    body: ConsumableIn,
    user: CurrentUser = Depends(get_current_user),
    machines: MachineRepository = Depends(get_machines),
    parts: PartsRepository = Depends(get_parts),
):
    user.assert_can_write()
    machine = machines.get(body.machine_id)
    if machine is None or machine["company_code"] != user.company_code:
        raise HTTPException(status_code=404, detail="machine not found")
    consumable_id = parts.next_item_id("consumables")
    row = {"consumable_id": consumable_id, "company_code": user.company_code, **body.model_dump()}
    parts.add_item("consumables", row)
    return row


@router.patch("/consumables/{consumable_id}")
def update_consumable(
    consumable_id: str,
    body: ConsumableUpdate,
    user: CurrentUser = Depends(get_current_user),
    parts: PartsRepository = Depends(get_parts),
):
    item = parts.get_item("consumables", consumable_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(item["company_code"])
    user.assert_can_write()
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    parts.update_item("consumables", consumable_id, updates)
    return parts.get_item("consumables", consumable_id)


@router.delete("/consumables/{consumable_id}", status_code=204)
def delete_consumable(
    consumable_id: str,
    user: CurrentUser = Depends(get_current_user),
    parts: PartsRepository = Depends(get_parts),
):
    item = parts.get_item("consumables", consumable_id)
    if item is None:
        raise HTTPException(status_code=404, detail="not found")
    user.assert_same_company(item["company_code"])
    user.assert_can_write()
    parts.delete_item("consumables", consumable_id)
    return Response(status_code=204)
