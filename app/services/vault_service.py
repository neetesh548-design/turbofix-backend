"""Vault service — document upload, download, and delete business logic.

Previously mixed into vault_router.py alongside HTTP concerns.
Now a clean service that vault_router.py delegates to.
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from app.auth import CurrentUser
from app.infrastructure.file_storage import FileStorage, validate_upload, FileTooLargeError, UnsupportedFileTypeError
from app.infrastructure.logging import get_logger
from app.repositories.base import DOCUMENT_CATEGORIES, DocumentRepository, MachineRepository

log = get_logger("turbofix.vault")


def get_document_or_404(document_id: str, documents: DocumentRepository) -> dict:
    doc = documents.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="not found")
    return doc


async def upload_document(
    *,
    user: CurrentUser,
    machine_id: str,
    category: str,
    title: str,
    filename: str,
    content: bytes,
    machines: MachineRepository,
    documents: DocumentRepository,
    storage: FileStorage,
) -> dict:
    """Validate, store, and register a new document. Returns the saved document row."""
    user.assert_can_write()

    if category not in DOCUMENT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"category must be one of {DOCUMENT_CATEGORIES}",
        )

    # Verify machine belongs to this company
    machine = machines.get(machine_id)
    if machine is None or machine["company_code"] != user.company_code:
        raise HTTPException(status_code=404, detail="machine not found")

    try:
        validate_upload(filename, len(content))
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc))

    document_id = documents.next_document_id()
    storage_path = await storage.save(user.company_code, machine_id, document_id, filename, content)

    row = {
        "document_id": document_id,
        "company_code": user.company_code,
        "machine_id": machine_id,
        "category": category,
        "title": title,
        "file_name": filename,
        "storage_path": storage_path,
        "uploaded_by": user.user_id,
        "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
    }
    documents.add(row)
    log.info("document.uploaded", document_id=document_id, machine_id=machine_id, company=user.company_code)
    return row


async def download_document(
    *,
    document_id: str,
    user: CurrentUser,
    documents: DocumentRepository,
    storage: FileStorage,
) -> tuple[bytes, str]:
    """Return (file_bytes, filename) for the document. Raises 404 if not found."""
    doc = get_document_or_404(document_id, documents)
    user.assert_same_company(doc["company_code"])
    content = await storage.read(doc["storage_path"])
    return content, doc["file_name"]


async def delete_document(
    *,
    document_id: str,
    user: CurrentUser,
    documents: DocumentRepository,
    storage: FileStorage,
) -> None:
    """Delete the document file and its metadata row."""
    doc = get_document_or_404(document_id, documents)
    user.assert_same_company(doc["company_code"])
    user.assert_can_write()
    await storage.delete(doc["storage_path"])
    documents.delete(document_id)
    log.info("document.deleted", document_id=document_id, company=user.company_code)
