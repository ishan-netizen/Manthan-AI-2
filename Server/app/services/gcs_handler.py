"""
Google Cloud Storage handler — signed URLs, downloads, uploads, ffmpeg extraction.
"""

import datetime
import logging
import os
import subprocess
import tempfile
from typing import Tuple

from google.cloud import storage

logger = logging.getLogger(__name__)

GCS_BUCKET = os.getenv("GCS_BUCKET", "").strip()
GCS_SIGNER_SA = os.getenv("GCS_SIGNER_SA", "immersivedata-sandbox@appspot.gserviceaccount.com")

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
        service_account_email=GCS_SIGNER_SA,
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
        service_account_email=GCS_SIGNER_SA,
    )


def download_to_file(gcs_path: str, local_path: str) -> None:
    """Download a GCS blob to a local file."""
    blob_name = gcs_path.replace(f"gs://{GCS_BUCKET}/", "")
    blob = _bucket.blob(blob_name)
    blob.download_to_filename(local_path)
    logger.info(f"[GCS] Downloaded {gcs_path} to {local_path}")


def upload_file(local_path: str, gcs_path: str) -> None:
    """Upload a local file to GCS."""
    blob_name = gcs_path.replace(f"gs://{GCS_BUCKET}/", "")
    blob = _bucket.blob(blob_name)
    blob.upload_from_filename(local_path)
    logger.info(f"[GCS] Uploaded {local_path} to {gcs_path}")


def extract_audio(input_gcs_path: str, output_gcs_path: str) -> None:
    """Download video from GCS, extract audio via ffmpeg, upload audio back to GCS."""
    fd, tmp_video = tempfile.mkstemp(suffix="_video")
    os.close(fd)
    fd, tmp_audio = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)

    try:
        download_to_file(input_gcs_path, tmp_video)
        subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_video, "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k", tmp_audio],
            check=True, capture_output=True,
        )
        upload_file(tmp_audio, output_gcs_path)
        logger.info(f"[GCS] Audio extracted: {input_gcs_path} → {output_gcs_path}")
    finally:
        for p in [tmp_video, tmp_audio]:
            try:
                os.remove(p)
            except Exception:
                pass


def delete_blob(gcs_path: str) -> None:
    """Delete a blob from GCS."""
    blob_name = gcs_path.replace(f"gs://{GCS_BUCKET}/", "")
    blob = _bucket.blob(blob_name)
    blob.delete()
    logger.info(f"[GCS] Deleted {gcs_path}")
