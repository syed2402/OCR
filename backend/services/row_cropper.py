"""
Row-level image cropper for Stellantis audit sheets.

Splits a full page image into one crop per extracted row — for DISPLAY only
in the review screen. The full page is always sent to Gemini unchanged.

Detection pipeline (strategies tried in order):
  1. Horizontal line detection via morphological ops (best for printed tables)
  2. Horizontal projection profile (works when lines are faint/missing)
  3. Equal-division fallback (guaranteed result)

Preprocessing applied before detection:
  - Deskew (correct page tilt)
  - CLAHE contrast enhancement
  - Gaussian denoise
  - Adaptive thresholding
"""

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Vertical padding added above/below each detected boundary (pixels)
ROW_PADDING = 12

# Fraction of image height to treat as header (skip for row detection).
# Stellantis audit sheets have a header block that occupies ~25% of page height
# (company banner + document title + engine info row + column label row).
HEADER_SKIP_FRAC = 0.25
FOOTER_SKIP_FRAC = 0.03


# ─────────────────────────────────────────────────────────────────────────────
# Pre-processing
# ─────────────────────────────────────────────────────────────────────────────

def _preprocess(img_bgr: np.ndarray) -> np.ndarray:
    """Return an enhanced grayscale image suitable for line/structure detection."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    # CLAHE contrast enhancement
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # Gentle Gaussian denoise
    gray = cv2.GaussianBlur(gray, (3, 3), 0)

    return gray


def _deskew(img_bgr: np.ndarray) -> np.ndarray:
    """Correct small page tilt (≤ ±5°) using dominant horizontal line angle."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Detect lines with Hough
    lines = cv2.HoughLinesP(binary, 1, np.pi / 180, threshold=200,
                            minLineLength=img_bgr.shape[1] // 3, maxLineGap=20)
    if lines is None:
        return img_bgr

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if abs(x2 - x1) > 10:
            angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
            if abs(angle) < 5:          # only near-horizontal lines
                angles.append(angle)

    if not angles:
        return img_bgr

    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.3:         # negligible tilt
        return img_bgr

    h, w = img_bgr.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), median_angle, 1.0)
    rotated = cv2.warpAffine(img_bgr, M, (w, h),
                             flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
    logger.debug("Deskewed by %.2f°", median_angle)
    return rotated


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1 — Morphological horizontal line detection
# ─────────────────────────────────────────────────────────────────────────────

def _detect_lines_morphological(gray: np.ndarray, min_width_frac: float = 0.35) -> list[int]:
    """Return sorted y-midpoints of detected horizontal table lines."""
    h, w = gray.shape
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kernel_w = max(1, int(w * min_width_frac))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 1))
    horiz = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=2)
    # Dilate slightly to fill tiny gaps
    horiz = cv2.dilate(horiz, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 3)))

    contours, _ = cv2.findContours(horiz, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    y_vals = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        if cw >= w * 0.30:
            y_vals.append(y + ch // 2)

    return _merge_close(sorted(y_vals), gap=8)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — Horizontal projection profile
# ─────────────────────────────────────────────────────────────────────────────

def _detect_lines_projection(gray: np.ndarray, num_rows: int) -> list[int]:
    """
    Find row boundaries by looking for horizontal bands of low ink density
    (the "white space" between table rows).
    Returns (num_rows + 1) boundary y-values.
    """
    h, w = gray.shape
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Horizontal projection: count dark pixels per row
    projection = binary.sum(axis=1).astype(np.float32)

    # Smooth to reduce noise
    kernel_size = max(3, h // 200)
    projection = np.convolve(projection, np.ones(kernel_size) / kernel_size, mode='same')

    # Skip the header block and footer — data rows live in between
    top = int(h * HEADER_SKIP_FRAC)
    bottom = int(h * (1.0 - FOOTER_SKIP_FRAC))
    body = projection[top:bottom]

    # Find local minima (gaps between rows) — look for (num_rows - 1) valleys
    threshold = body.max() * 0.15       # below 15% of max is "gap"
    gap_mask = (body < threshold).astype(np.uint8)

    # Label connected gap regions
    from scipy import ndimage as ndi
    labeled, n_labels = ndi.label(gap_mask)
    gap_centers = []
    for i in range(1, n_labels + 1):
        ys = np.where(labeled == i)[0]
        gap_centers.append(int(ys.mean()) + top)

    # If we found enough gaps, use them
    if len(gap_centers) >= num_rows - 1:
        boundaries = [top] + sorted(gap_centers)[: num_rows - 1] + [bottom]
        return boundaries

    # Otherwise fall back to equal division
    return _equal_division(h, num_rows)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3 — Equal division fallback
# ─────────────────────────────────────────────────────────────────────────────

def _equal_division(h: int, num_rows: int, header_frac: float = HEADER_SKIP_FRAC) -> list[int]:
    top = int(h * header_frac)
    bottom = int(h * (1.0 - FOOTER_SKIP_FRAC))
    row_h = (bottom - top) // num_rows
    return [top + i * row_h for i in range(num_rows + 1)]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _merge_close(vals: list[int], gap: int = 8) -> list[int]:
    merged: list[int] = []
    for v in vals:
        if merged and abs(v - merged[-1]) <= gap:
            merged[-1] = (merged[-1] + v) // 2
        else:
            merged.append(v)
    return merged


def _boundaries_from_lines(lines: list[int], num_rows: int, h: int) -> list[int] | None:
    """
    Given detected horizontal line positions, extract (num_rows + 1) boundaries
    that enclose exactly num_rows data rows.

    Strategy: discard all lines that fall inside the header block (top HEADER_SKIP_FRAC
    of the image), then take the first num_rows+1 remaining lines so we always start
    at the first actual data row — not at a header column-label line.
    """
    # Drop header-region lines
    data_lines = [l for l in sorted(lines) if l > h * HEADER_SKIP_FRAC]

    if len(data_lines) < num_rows + 1:
        return None

    # Take the first num_rows+1 lines starting from the data area
    candidates = data_lines[:num_rows + 1]

    # Sanity: boundaries should be reasonably spread across the page
    span = candidates[-1] - candidates[0]
    if span < h * 0.25:
        return None

    return candidates


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def crop_rows(
    image_path: str,
    num_rows: int,
    output_dir: str,
    upload_id: str,
    page_num: int,
) -> list[str]:
    """
    Crop `image_path` into `num_rows` row-level images for review display.

    Full-page image sent to Gemini is never touched — cropping is post-OCR only.
    Guarantees exactly `num_rows` output paths (falls back gracefully).
    """
    if num_rows == 0:
        return []

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Load original
    img_bgr = cv2.imread(image_path)
    if img_bgr is None:
        logger.warning("row_cropper: cannot load %s — using full page for all rows", image_path)
        return [image_path] * num_rows

    h, w = img_bgr.shape[:2]

    # ── Pre-process ───────────────────────────────────────────────────────────
    img_bgr = _deskew(img_bgr)
    gray = _preprocess(img_bgr)

    # ── Determine row boundaries ──────────────────────────────────────────────
    boundaries: list[int] | None = None
    strategy_used = "unknown"

    # Strategy 1: morphological line detection
    try:
        lines = _detect_lines_morphological(gray)
        boundaries = _boundaries_from_lines(lines, num_rows, h)
        if boundaries:
            strategy_used = f"morphological ({len(lines)} lines detected)"
    except Exception as e:
        logger.debug("row_cropper morph failed page %d: %s", page_num, e)

    # Strategy 2: projection profile
    if boundaries is None:
        try:
            boundaries = _detect_lines_projection(gray, num_rows)
            if boundaries and len(boundaries) >= num_rows + 1:
                strategy_used = "projection profile"
            else:
                boundaries = None
        except Exception as e:
            logger.debug("row_cropper projection failed page %d: %s", page_num, e)

    # Strategy 3: equal division
    if boundaries is None:
        boundaries = _equal_division(h, num_rows)
        strategy_used = "equal division (fallback)"

    logger.info(
        "row_cropper: page %d — %d rows via %s",
        page_num, num_rows, strategy_used,
    )

    # ── Crop and save ─────────────────────────────────────────────────────────
    cropped_paths: list[str] = []

    for i in range(num_rows):
        y_top = boundaries[i] if i < len(boundaries) else 0
        y_bot = boundaries[i + 1] if i + 1 < len(boundaries) else h

        # Apply safe padding — expand crop to avoid clipping handwriting
        y0 = max(0, y_top - ROW_PADDING)
        y1 = min(h, y_bot + ROW_PADDING)

        # Minimum sensible height
        if y1 - y0 < 20:
            y0 = max(0, (y_top + y_bot) // 2 - 30)
            y1 = min(h, (y_top + y_bot) // 2 + 30)

        crop = img_bgr[y0:y1, :]

        out_name = f"{upload_id}_page_{page_num:03d}_row_{i + 1:03d}.png"
        out_path = str(Path(output_dir) / out_name)
        try:
            cv2.imwrite(out_path, crop)
            cropped_paths.append(out_path)
        except Exception as exc:
            logger.warning("row_cropper: save failed %s: %s", out_path, exc)
            cropped_paths.append(image_path)

    return cropped_paths
