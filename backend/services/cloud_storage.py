"""
Optional Cloudinary image storage.

If Cloudinary credentials are configured, generated PDF page images are uploaded
there and the secure URL is stored for review display. Local files remain the
fallback for development and deployments without persistent storage.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import cloudinary
import cloudinary.uploader

logger = logging.getLogger(__name__)


def _configured() -> bool:
    if os.getenv("CLOUDINARY_URL"):
        return True
    return all(
        os.getenv(name)
        for name in ("CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET")
    )


def _configure() -> None:
    if os.getenv("CLOUDINARY_URL"):
        cloudinary.config(secure=True)
        return

    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
        api_key=os.getenv("CLOUDINARY_API_KEY"),
        api_secret=os.getenv("CLOUDINARY_API_SECRET"),
        secure=True,
    )


def upload_review_image(image_path: str, upload_id: str, page_num: int) -> Optional[str]:
    """Upload a generated page image and return its public HTTPS URL."""
    if not _configured():
        return None

    path = Path(image_path)
    if not path.exists():
        logger.warning("Cloudinary upload skipped; file missing: %s", image_path)
        return None

    try:
        _configure()
        result = cloudinary.uploader.upload(
            str(path),
            folder=f"ocr-analytics/{upload_id}",
            public_id=f"page_{page_num:03d}",
            overwrite=True,
            resource_type="image",
        )
        url = result.get("secure_url")
        logger.info("Uploaded page %d image to Cloudinary: %s", page_num, url)
        return url
    except Exception as exc:
        logger.warning("Cloudinary upload failed for %s: %s", image_path, exc)
        return None
