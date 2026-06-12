"""
Google Cloud Storage handler — signed URLs, downloads, uploads.
"""

import datetime
import logging
import os
from typing import Tuple

from google.cloud import storage

logger = logging.getLogger(__name__)

GCS_BUCKET = os.getenv("GCS_BUCKET", "").strip()

if GCS_BUCKET:
    _client = storage.Client()
    _bucket = _client.bucket(GCS_BUCKET)
else:
    _bucket = None


def is_ready() -> bool:
    return _bucket is not None


def generate_upload_url(filename: str, content_type: str) -> Tuple[str, str]:
    """Generate a signed upload URL for direct GCS PUT. Returns (url, gcs_path)."""
    blob = _bucket.blob(filename)
    url = blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(minutes=15),
        method="PUT",
        content_type=content_type,
    )
    return url, f"gs://{GCS_BUCKET}/{filename}"


def generate_download_url(gcs_path: str, minutes: int = 60) -> str:
    """Generate a signed download URL for reading from GCS."""
    blob_name = gcs_path.replace(f"gs://{GCS_BUCKET}/", "")
    blob = _bucket.blob(blob_name)
    return blob.generate_signed_url(
        version="v4",
        expiration=datetime.timedelta(minutes=minutes),
        method="GET",
    )


def download_to_file(gcs_path: str, local_path: str) -> None:
    """Download a GCS blob to a local file."""
    blob_name = gcs_path.replace(f"gs://{GCS_BUCKET}/", "")
    blob = _bucket.blob(blob_name)
    blob.download_to_filename(local_path)
    logger.info(f"[GCS] Downloaded {gcs_path} to {local_path}")


def delete_blob(gcs_path: str) -> None:
    """Delete a blob from GCS."""
    blob_name = gcs_path.replace(f"gs://{GCS_BUCKET}/", "")
    blob = _bucket.blob(blob_name)
    blob.delete()
    logger.info(f"[GCS] Deleted {gcs_path}")
