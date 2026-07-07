"""Pluggable storage for the actual bytes of an uploaded document (manual PDF,
circuit/hydraulic diagram image, etc.) - metadata about *what* was uploaded lives in
documents_store.py; this module only knows how to save/read/delete the file itself.

Mirrors the TICKET_STORE local/sheets split in store.py: DOCUMENT_STORE=local (the
default) needs no credentials and is what tests run against; DOCUMENT_STORE=gcs
uploads to a Google Cloud Storage bucket using the same service-account file already
used for Sheets access (GOOGLE_SERVICE_ACCOUNT_FILE), keeping the project on one cloud
vendor rather than introducing a second one just for file storage. Like
store_sheets.py, the gcs path is written for real but has never been exercised
against an actual bucket in this sandbox - it needs a real GOOGLE_SERVICE_ACCOUNT_FILE
and GCS_BUCKET_NAME to validate.
"""

from pathlib import Path

from app import config


class UnsupportedFileTypeError(Exception):
    pass


class FileTooLargeError(Exception):
    pass


def validate_upload(filename: str, size_bytes: int) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in config.ALLOWED_DOCUMENT_EXTENSIONS:
        raise UnsupportedFileTypeError(
            f"'{ext}' is not an allowed document type ({sorted(config.ALLOWED_DOCUMENT_EXTENSIONS)})"
        )
    max_bytes = config.MAX_DOCUMENT_SIZE_MB * 1024 * 1024
    if size_bytes > max_bytes:
        raise FileTooLargeError(f"file is {size_bytes} bytes, over the {config.MAX_DOCUMENT_SIZE_MB}MB limit")


def _object_key(company_code: str, machine_id: str, document_id: str, filename: str) -> str:
    safe_name = Path(filename).name  # strip any path components a client might send
    return f"{company_code}/{machine_id}/{document_id}_{safe_name}"


def save_file(company_code: str, machine_id: str, document_id: str, filename: str, content: bytes) -> str:
    """Saves the file and returns a storage_path that read_file/delete_file can use
    later to locate it again (an absolute local path, or a gs:// URI)."""
    if config.DOCUMENT_STORE == "gcs":
        return _save_gcs(company_code, machine_id, document_id, filename, content)
    return _save_local(company_code, machine_id, document_id, filename, content)


def read_file(storage_path: str) -> bytes:
    if storage_path.startswith("gs://"):
        return _read_gcs(storage_path)
    return Path(storage_path).read_bytes()


def delete_file(storage_path: str) -> None:
    if storage_path.startswith("gs://"):
        _delete_gcs(storage_path)
        return
    path = Path(storage_path)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# local disk backend
# ---------------------------------------------------------------------------

def _save_local(company_code: str, machine_id: str, document_id: str, filename: str, content: bytes) -> str:
    key = _object_key(company_code, machine_id, document_id, filename)
    path = config.DOCUMENT_STORE_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


# ---------------------------------------------------------------------------
# Google Cloud Storage backend (untested against a real bucket - see module docstring)
# ---------------------------------------------------------------------------

def _gcs_client():
    from google.cloud import storage  # imported lazily so this dependency is only
    # required when DOCUMENT_STORE=gcs is actually selected

    if config.GOOGLE_SERVICE_ACCOUNT_FILE:
        return storage.Client.from_service_account_json(config.GOOGLE_SERVICE_ACCOUNT_FILE)
    return storage.Client()


def _save_gcs(company_code: str, machine_id: str, document_id: str, filename: str, content: bytes) -> str:
    key = _object_key(company_code, machine_id, document_id, filename)
    client = _gcs_client()
    bucket = client.bucket(config.GCS_BUCKET_NAME)
    blob = bucket.blob(key)
    blob.upload_from_string(content)
    return f"gs://{config.GCS_BUCKET_NAME}/{key}"


def _parse_gs_uri(uri: str):
    without_scheme = uri[len("gs://"):]
    bucket_name, _, key = without_scheme.partition("/")
    return bucket_name, key


def _read_gcs(storage_path: str) -> bytes:
    bucket_name, key = _parse_gs_uri(storage_path)
    client = _gcs_client()
    blob = client.bucket(bucket_name).blob(key)
    return blob.download_as_bytes()


def _delete_gcs(storage_path: str) -> None:
    bucket_name, key = _parse_gs_uri(storage_path)
    client = _gcs_client()
    client.bucket(bucket_name).blob(key).delete()
