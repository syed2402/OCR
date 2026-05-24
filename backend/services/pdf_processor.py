"""
PDF to page images pipeline.

Uses pdf2image, which wraps Poppler, to convert each PDF page into a
high-resolution PNG. The images are written to ROW_IMAGES_DIR and the list of
file paths is returned for downstream preprocessing and OCR.
"""

import logging
import os
import re
from pathlib import Path
from typing import Callable, Optional

from pdf2image import convert_from_path, pdfinfo_from_path
from pdf2image.exceptions import PDFPopplerTimeoutError
from PIL import Image

logger = logging.getLogger(__name__)

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Sharp enough for handwritten audit sheets. Hosted CPUs such as Render can be
# slow at rasterizing very large PDF pages, so these defaults favor reliability
# over ultra-high resolution. They can be raised with env vars when needed.
DEFAULT_RENDER_DPI = _int_env("PDF_RENDER_DPI", 180)
MIN_RENDER_DPI = _int_env("PDF_MIN_RENDER_DPI", 72)
MAX_RENDER_LONG_EDGE_PX = _int_env("PDF_MAX_RENDER_LONG_EDGE_PX", 2600)
PAGE_RENDER_TIMEOUT_SECONDS = _int_env("PDF_RENDER_TIMEOUT_SECONDS", 300)
FALLBACK_RENDER_DPIS = [96, 72]


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


def _render_dpi_from_pdf_info(info: dict) -> int:
    page_size = str(info.get("Page size", ""))
    match = re.search(r"([0-9.]+)\s+x\s+([0-9.]+)\s+pts", page_size)
    if not match:
        return DEFAULT_RENDER_DPI

    width_pt = float(match.group(1))
    height_pt = float(match.group(2))
    longest_edge_pt = max(width_pt, height_pt)
    if longest_edge_pt <= 0:
        return DEFAULT_RENDER_DPI

    dpi_for_cap = int(MAX_RENDER_LONG_EDGE_PX * 72 / longest_edge_pt)
    return max(MIN_RENDER_DPI, min(DEFAULT_RENDER_DPI, dpi_for_cap))


def pdf_to_images(
    pdf_path: str,
    output_dir: str,
    upload_id: str,
    poppler_path: Optional[str] = None,
    on_page_saved: Optional[Callable[[int, str], None]] = None,
) -> list[str]:
    """
    Convert every page of a PDF to a PNG image.

    Returns a list of absolute paths to the generated PNG files, ordered by
    page number.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    poppler_path = resolve_poppler_path(poppler_path)

    logger.info("Using poppler_path: %s", poppler_path or "(system PATH)")

    info_kwargs: dict = {"pdf_path": pdf_path}
    if poppler_path:
        info_kwargs["poppler_path"] = poppler_path

    try:
        pdf_info = pdfinfo_from_path(**info_kwargs)
        page_count = int(pdf_info.get("Pages", 0))
    except Exception as exc:
        raise RuntimeError(f"Could not read PDF page count: {exc}") from exc

    if page_count <= 0:
        raise RuntimeError("PDF does not contain any readable pages.")

    render_dpi = _render_dpi_from_pdf_info(pdf_info)
    logger.info(
        "Converting PDF %s to images at %d DPI (page size: %s)",
        pdf_path,
        render_dpi,
        pdf_info.get("Page size", "unknown"),
    )

    image_paths: list[str] = []
    for page_num in range(1, page_count + 1):
        pages: list[Image.Image] = []
        attempted_dpis: list[int] = []
        last_timeout: PDFPopplerTimeoutError | None = None
        for dpi in [render_dpi, *FALLBACK_RENDER_DPIS]:
            if dpi in attempted_dpis or dpi > render_dpi:
                continue
            attempted_dpis.append(dpi)
            kwargs: dict = {
                "pdf_path": pdf_path,
                "dpi": dpi,
                "fmt": "png",
                "first_page": page_num,
                "last_page": page_num,
                "thread_count": 1,
                "timeout": PAGE_RENDER_TIMEOUT_SECONDS,
            }
            if poppler_path:
                kwargs["poppler_path"] = poppler_path

            logger.info("Rendering page %d/%d at %d DPI", page_num, page_count, dpi)
            try:
                pages = convert_from_path(**kwargs)
                break
            except PDFPopplerTimeoutError as exc:
                last_timeout = exc
                logger.warning(
                    "PDF page %d timed out after %d seconds at %d DPI; trying lower DPI if available.",
                    page_num,
                    PAGE_RENDER_TIMEOUT_SECONDS,
                    dpi,
                )

        if not pages and last_timeout is not None:
            attempted = ", ".join(f"{dpi} DPI" for dpi in attempted_dpis)
            raise RuntimeError(
                f"PDF page {page_num} took longer than "
                f"{PAGE_RENDER_TIMEOUT_SECONDS} seconds to render at {attempted}."
            ) from last_timeout

        if not pages:
            raise RuntimeError(f"PDF page {page_num} did not render to an image.")

        filename = f"{upload_id}_page_{page_num:03d}.png"
        dest = output_dir / filename
        pages[0].save(str(dest), "PNG")
        image_paths.append(str(dest))
        if on_page_saved:
            on_page_saved(page_num, str(dest))
        logger.debug("Saved page %d -> %s", page_num, dest)

    logger.info("Converted %d page(s) from %s", len(image_paths), pdf_path)
    return image_paths
