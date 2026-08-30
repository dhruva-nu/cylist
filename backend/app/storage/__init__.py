"""Blob storage.

Where uploaded file *contents* live. The database records what a file is
called and which folder it is in; this package holds the bytes.
"""

from fastapi import Depends

from app.config import Settings, app_settings
from app.storage.base import BlobStore, BlobTooLargeError, StoredBlob
from app.storage.local import LocalBlobStore

__all__ = [
    "BlobStore",
    "BlobTooLargeError",
    "LocalBlobStore",
    "StoredBlob",
    "get_blob_store",
]


def get_blob_store(settings: Settings = Depends(app_settings)) -> BlobStore:
    """FastAPI dependency returning the store this app was configured with.

    Reads the settings off the request rather than the environment for the same
    reason :func:`app.config.app_settings` exists: a test builds an app pointed
    at a temporary data directory, and its uploads have to land there.
    """
    return LocalBlobStore(settings.blob_dir)
