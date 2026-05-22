"""
PDF → page images pipeline.

Uses pdf2image (which wraps poppler) to convert each PDF page into a
high-resolution PNG.  The images are written to ROW_IMAGES_DIR and the
list of file paths is returned for downstream preprocessing + OCR.

Windows note: poppler binaries must be on PATH or POPPLER_PATH env var
must be set.  See README for download link.
"""

import os
import logging
from pathlib import Path
from typing import Optional

from pdf2image import convert_from_path
from PIL import Image

logger = logging.getLogger(__name__)

# 220 DPI — sharp enough for handwritten audit sheets, ~2x faster than 300 DPI
RENDER_DPI = 300


def resolve_poppler_path(poppler_path: Optional[str] = None) -> Optional[str]:
    """
    Resolve the Poppler bin folder used by pdf2image.

    Priority: explicit path, POPPLER_PATH, repo-local poppler_bin, then PATH.
    """
    repo_poppler = Path(__file__).resolve().parents[2] / "poppler_bin"
    candidates = [poppler_path, os.getenv("POPPLER_PATH"), str(repo_poppler)]

    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if (path / "pdfinfo.exe").exists() or (path / "pdfinfo").exists():
            resolved = str(path)
            if resolved not in os.environ.get("PATH", ""):
                os.environ["PATH"] = resolved + os.pathsep + os.environ.get("PATH", "")
            return resolved

    return None


def pdf_to_images(
    pdf_path: str,
    output_dir: str,
    upload_id: str,
    poppler_path: Optional[str] = None,
) -> list[str]:
    """
    Convert every page of a PDF to a PNG image.

    Returns a list of absolute paths to the generated PNG files,
    ordered by page number.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve poppler path: explicit arg → env var → rely on system PATH
    poppler_path = resolve_poppler_path(poppler_path)

    kwargs: dict = {
        "pdf_path": pdf_path,
        "dpi": RENDER_DPI,
        "fmt": "png",
        "thread_count": 2,
    }
    if poppler_path:
        kwargs["poppler_path"] = poppler_path

    logger.info("Using poppler_path: %s", poppler_path or "(system PATH)")

    logger.info("Converting PDF %s to images at %d DPI", pdf_path, RENDER_DPI)

    pages: list[Image.Image] = convert_from_path(**kwargs)

    image_paths: list[str] = []
    for page_num, page_image in enumerate(pages, start=1):
        filename = f"{upload_id}_page_{page_num:03d}.png"
        dest = output_dir / filename
        page_image.save(str(dest), "PNG")
        image_paths.append(str(dest))
        logger.debug("Saved page %d → %s", page_num, dest)

    logger.info("Converted %d page(s) from %s", len(pages), pdf_path)
    return image_paths
