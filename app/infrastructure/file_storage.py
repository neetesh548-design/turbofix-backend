"""Pluggable file storage — local disk (dev/test) and Google Drive (production).

The active backend is selected by DOCUMENT_STORE env var:
  - "local"  (default) — saves files to disk; no credentials needed; files are
    LOST on Railway redeploys since the filesystem is ephemeral.
  - "drive"  (production) — uploads to a Google Drive folder using the same
    service-account already configured for Google Sheets. Files survive redeploys.
  - "gcs"    (legacy stub) — left for backward compatibility, maps to local.

The abstract FileStorage class defines the interface; both implementations
satisfy it so callers can be dependency-injected without knowing which is active.

Usage:
    from app.infrastructure.file_storage import get_file_storage
    storage = get_file_storage()
    path = await storage.save(company_code, machine_id, doc_id, filename, content)
    content = await storage.read(path)
    await storage.delete(path)
"""

import io
from abc import ABC, abstractmethod
from pathlib import Path

from app import config
from app.infrastructure.logging import get_logger

log = get_logger("turbofix.storage")


class UnsupportedFileTypeError(Exception):
    pass


class FileTooLargeError(Exception):
    pass


def validate_upload(filename: str, size_bytes: int) -> None:
    """Shared upload validation — called before any save attempt."""
    ext = Path(filename).suffix.lower()
    if ext not in config.ALLOWED_DOCUMENT_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"'{ext}' is not an allowed document type ({sorted(config.ALLOWED_DOCUMENT_EXTENSIONS)})"
        )
    max_bytes = config.MAX_DOCUMENT_SIZE_MB * 1024 * 1024
    if size_bytes > max_bytes:
        raise FileTooLargeError(
            f"file is {size_bytes} bytes, over the {config.MAX_DOCUMENT_SIZE_MB} MB limit"
        )


def _object_key(company_name: str, machine_name: str, category: str, title: str, document_id: str, filename: str) -> str:
    safe_name = Path(filename).name
    safe = lambda s: s.replace("/", "_").replace("\\", "_").strip()
    return f"{safe(company_name)}/{safe(machine_name)}/{safe(category)}/{safe(title)}/{document_id}_{safe_name}"


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class FileStorage(ABC):
    @abstractmethod
    async def save(
        self, company_name: str, machine_name: str, category: str, title: str,
        document_id: str, filename: str, content: bytes,
    ) -> str:
        """Save file bytes and return an opaque storage_path string for later retrieval."""

    @abstractmethod
    async def read(self, storage_path: str) -> bytes:
        """Return file bytes for a storage_path previously returned by save()."""

    @abstractmethod
    async def delete(self, storage_path: str) -> None:
        """Delete the file at storage_path. Silently ignores missing files."""


# ---------------------------------------------------------------------------
# Local disk backend (dev / test)
# ---------------------------------------------------------------------------

class LocalFileStorage(FileStorage):
    """Stores files on the local filesystem under DOCUMENT_STORE_DIR.

    WARNING: Railway's filesystem is ephemeral — files stored here are lost on
    redeploy.  Use DriveFileStorage in production.
    """

    def __init__(self, base_dir: Path):
        self._base = base_dir

    async def save(self, company_name, machine_name, category, title, document_id, filename, content) -> str:
        key = _object_key(company_name, machine_name, category, title, document_id, filename)
        path = self._base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        log.info("storage.saved", backend="local", path=str(path))
        return str(path)

    async def read(self, storage_path: str) -> bytes:
        return Path(storage_path).read_bytes()

    async def delete(self, storage_path: str) -> None:
        path = Path(storage_path)
        if path.exists():
            path.unlink()
            log.info("storage.deleted", backend="local", path=storage_path)


# ---------------------------------------------------------------------------
# Google Drive backend (production — free 15 GB, same service account)
# ---------------------------------------------------------------------------

class DriveFileStorage(FileStorage):
    """Stores files in a Google Drive folder using the Drive API.

    Files are organised as:  TurboFix-Docs/{company}/{machine}/{doc_id}_{name}
    The Drive file ID is returned as storage_path (used later for read/delete).
    """

    def __init__(self, service_account_file: str, drive_folder_id: str, owner_email: str = ""):
        self._sa_file = service_account_file
        self._folder_id = drive_folder_id
        self._owner_email = owner_email

    def _service(self):
        """Build a Drive API service (lazy import — not needed in local mode)."""
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build

        creds = Credentials.from_service_account_file(
            self._sa_file,
            scopes=["https://www.googleapis.com/auth/drive"],
        )
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    def _get_or_create_folder(self, drive, parent_id: str, folder_name: str) -> str:
        """Find or create a subfolder under parent_id. Returns the folder ID."""
        safe_name = folder_name.replace("/", "_").replace("\\", "_").strip()
        escaped_name = safe_name.replace("'", "\\'")
        query = (
            f"'{parent_id}' in parents and name = '{escaped_name}' "
            f"and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        )
        results = drive.files().list(
            q=query, fields="files(id)", pageSize=1, supportsAllDrives=True,
        ).execute()
        if results.get("files"):
            return results["files"][0]["id"]
        meta = {"name": safe_name, "parents": [parent_id], "mimeType": "application/vnd.google-apps.folder"}
        folder = drive.files().create(body=meta, fields="id", supportsAllDrives=True).execute()
        log.info("storage.folder_created", name=safe_name, folder_id=folder["id"])
        return folder["id"]

    async def save(self, company_name, machine_name, category, title, document_id, filename, content) -> str:
        from googleapiclient.http import MediaIoBaseUpload

        drive = self._service()
        folder_id = self._folder_id
        try:
            for folder_name in [company_name, machine_name, category, title]:
                folder_id = self._get_or_create_folder(drive, folder_id, folder_name)

            safe_name = f"{document_id}_{Path(filename).name}"
            file_metadata = {"name": safe_name, "parents": [folder_id]}
            media = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/octet-stream")
            result = drive.files().create(
                body=file_metadata, media_body=media, fields="id",
                supportsAllDrives=True,
            ).execute()
            file_id = result["id"]
            log.info("storage.saved", backend="drive", file_id=file_id, name=safe_name)
            return file_id
        except Exception as exc:
            log.error("storage.drive_error", error=str(exc), company=company_name, machine=machine_name)
            raise

    async def read(self, storage_path: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        drive = self._service()
        request = drive.files().get_media(fileId=storage_path)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue()

    async def delete(self, storage_path: str) -> None:
        drive = self._service()
        try:
            drive.files().delete(fileId=storage_path).execute()
            log.info("storage.deleted", backend="drive", file_id=storage_path)
        except Exception as exc:
            log.warning("storage.delete_failed", file_id=storage_path, error=str(exc))


# ---------------------------------------------------------------------------
# Cloudflare R2 backend (production — free 10 GB, S3-compatible)
# ---------------------------------------------------------------------------

class R2FileStorage(FileStorage):
    """Stores files in Cloudflare R2 (S3-compatible). Free 10 GB storage."""

    def __init__(self, account_id: str, access_key_id: str, secret_access_key: str, bucket_name: str):
        self._bucket_name = bucket_name
        self._endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key

    def _client(self):
        import boto3
        return boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            region_name="auto",
        )

    async def save(self, company_name, machine_name, category, title, document_id, filename, content) -> str:
        key = _object_key(company_name, machine_name, category, title, document_id, filename)
        client = self._client()
        client.put_object(Bucket=self._bucket_name, Key=key, Body=content)
        log.info("storage.saved", backend="r2", key=key)
        return key

    async def read(self, storage_path: str) -> bytes:
        client = self._client()
        resp = client.get_object(Bucket=self._bucket_name, Key=storage_path)
        return resp["Body"].read()

    async def delete(self, storage_path: str) -> None:
        client = self._client()
        try:
            client.delete_object(Bucket=self._bucket_name, Key=storage_path)
            log.info("storage.deleted", backend="r2", key=storage_path)
        except Exception as exc:
            log.warning("storage.delete_failed", key=storage_path, error=str(exc))


# ---------------------------------------------------------------------------
# Factory — returns the configured implementation
# ---------------------------------------------------------------------------

def get_file_storage() -> FileStorage:
    """Return the FileStorage implementation selected by DOCUMENT_STORE env var."""
    if config.DOCUMENT_STORE == "r2" and config.R2_ACCOUNT_ID:
        return R2FileStorage(
            config.R2_ACCOUNT_ID, config.R2_ACCESS_KEY_ID,
            config.R2_SECRET_ACCESS_KEY, config.R2_BUCKET_NAME,
        )
    if config.DOCUMENT_STORE == "drive" and config.GOOGLE_DRIVE_FOLDER_ID:
        return DriveFileStorage(config.GOOGLE_SERVICE_ACCOUNT_FILE, config.GOOGLE_DRIVE_FOLDER_ID)
    return LocalFileStorage(config.DOCUMENT_STORE_DIR)
