"""
OpenCV-based image preprocessing pipeline.

Applied to each page image before sending to Gemini to improve OCR accuracy:
  1. Deskew  — detect dominant text angle and rotate to level
  2. CLAHE   — contrast limited adaptive histogram equalisation
  3. Denoise — bilateral filter preserves edges while removing noise
  4. Sharpen — unsharp mask to enhance text edges

The preprocessed image is saved alongside the original (suffix _proc).
"""

import logging
import math
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual steps
# ---------------------------------------------------------------------------

def _deskew(gray: np.ndarray) -> np.ndarray:
    """Detect the dominant text angle and rotate the image to be level."""
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, math.pi / 180, threshold=100, minLineLength=100, maxLineGap=10
    )

    if lines is None:
        return gray

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        if x2 != x1:
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            if abs(angle) < 15:  # only use near-horizontal lines
                angles.append(angle)

    if not angles:
        return gray

    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.5:
        return gray  # already level enough

    h, w = gray.shape
    centre = (w // 2, h // 2)
    rotation_matrix = cv2.getRotationMatrix2D(centre, median_angle, 1.0)
    rotated = cv2.warpAffine(
        gray, rotation_matrix, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    logger.debug("Deskewed by %.2f degrees", median_angle)
    return rotated


def _apply_clahe(gray: np.ndarray) -> np.ndarray:
    """Boost local contrast using CLAHE - enhanced for handwritten text."""
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _denoise(gray: np.ndarray) -> np.ndarray:
    """Remove noise while preserving edges (bilateral filter)."""
    return cv2.bilateralFilter(gray, d=9, sigmaColor=75, sigmaSpace=75)


def _sharpen(gray: np.ndarray) -> np.ndarray:
    """Unsharp mask to make text crisper - enhanced for handwritten numbers."""
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=2)
    sharpened = cv2.addWeighted(gray, 1.8, blurred, -0.8, 0)
    return sharpened


def _enhance_handwriting(gray: np.ndarray) -> np.ndarray:
    """Additional enhancement specifically for handwritten measurements."""
    # Apply adaptive thresholding to make handwritten text stand out
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
    )
    # Invert if needed (make text dark on light background)
    if np.mean(binary) < 127:
        binary = cv2.bitwise_not(binary)
    # Slight morphological closing to connect broken characters
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    return closed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preprocess_image(image_path: str, enhance_handwriting: bool = True) -> str:
    """
    Run the full preprocessing pipeline on a page image.

    Saves the result as <original_stem>_proc.png next to the source file.
    Returns the path to the preprocessed image.
    
    Args:
        image_path: Path to the source image
        enhance_handwriting: If True, apply additional handwriting enhancement
    """
    src = Path(image_path)
    dest = src.parent / f"{src.stem}_proc.png"

    bgr = cv2.imread(str(src))
    if bgr is None:
        logger.error("Could not read image: %s", src)
        return image_path  # fall back to unprocessed

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    gray = _deskew(gray)
    gray = _apply_clahe(gray)
    gray = _denoise(gray)
    gray = _sharpen(gray)
    
    if enhance_handwriting:
        gray = _enhance_handwriting(gray)

    cv2.imwrite(str(dest), gray)
    logger.info("Preprocessed image saved to %s (handwriting_enhanced=%s)", dest, enhance_handwriting)
    return str(dest)
