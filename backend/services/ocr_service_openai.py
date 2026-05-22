"""
Stellantis OCR extraction using OpenAI GPT-4o Vision.

GPT-4o has excellent vision capabilities and is particularly good at:
- Handwritten text recognition
- Structured data extraction
- Following complex instructions
"""

import base64
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OPENAI_MODEL = "gpt-4o"  # GPT-4o with vision

_EXTRACTION_PROMPT = """You are analyzing a Stellantis manufacturing TORQUE AUDIT SHEET. Your PRIMARY GOAL is to extract ALL handwritten measurement values with 100% completeness.

CRITICAL: This is a quality control document. Missing even ONE measurement value is a FAILURE. You must extract EVERY SINGLE handwritten number.

TASK: Extract ALL data rows and return them as a JSON array.

For EACH data row, return this JSON object:
{
  "operation_number": "1140",
  "engine_number": "105925",
  "process_description": "MB Cap tightening",
  "measurements": [21.20, 18.20, 19.40],
  "judgement": "OK",
  "audit_date": "2026-05-05",
  "confidence": "HIGH",
  "unclear_fields": []
}

═══════════════════════════════════════════════════════════════════
MEASUREMENTS - CRITICAL INSTRUCTIONS (READ CAREFULLY):
═══════════════════════════════════════════════════════════════════

The "measurements" field is the MOST IMPORTANT field. These are handwritten torque values.

1. LOOK FOR THE MEASUREMENT COLUMNS:
   - Usually labeled "Actual", "Torque", "Measurement", or similar
   - Some rows have only 1-3 handwritten values, others have more
   - Values may be decimals such as 21.20, 18.20, 19.40, 10.55, 8.20

2. EXTRACT EVERY SINGLE VALUE:
   - Scan left to right across ALL measurement columns
   - Preserve decimal points exactly
   - DO NOT round decimals to whole numbers
   - DO NOT split one decimal value into multiple values
   - DO NOT repeat values to fill empty table cells
   - Ignore blank cells, crossed/X cells, printed tolerance text, and printed equipment ranges
   - If a row has 3 handwritten measurements, return 3 values, not 6
   - If handwriting is unclear, do not guess. Omit that value and add "measurements" to unclear_fields
   - Prefer an incomplete measurements array over a guessed wrong number

3. COMMON OCR PITFALLS TO AVOID:
   - 21.20 must not become 20 or [21, 20]
   - 18.20 must not become 18 or 20
   - 19.40 must not become 19 or 40
   - Blank crossed cells must not be copied from neighboring cells

4. DOUBLE-CHECK YOUR COUNT:
   - Count only visible handwritten numeric entries
   - The number of values in measurements MUST equal visible handwritten entries, not total grid cells

═══════════════════════════════════════════════════════════════════
OTHER FIELD RULES:
═══════════════════════════════════════════════════════════════════

operation_number:
  - Exactly 4 digits (e.g., "1140", "1070", "1050")
  - Fix OCR errors: O→0, l→1, I→1, S→5, Z→2, B→8
  - Usually in the leftmost column

engine_number:
  - 5-6 digit number (e.g., "105925")
  - May be at the top of the sheet or in a column

process_description:
  - Text description of the operation
  - Examples: "MB Cap tightening", "MB Cap removal & PCN tightening"

judgement:
  - Look for handwritten or stamped judgement
  - Normalize to: OK / NOK / DK / HT / NA
  - OK = Pass, Good, Acceptable, ✓
  - NOK = Not OK, Fail, NG, Bad, ✗
  - DK = Don't Know, Unclear, ?
  - HT = Hold, Pending
  - NA = Not Applicable, N/A, blank

audit_date:
  - Extract from page header (top of sheet)
  - Format: YYYY-MM-DD
  - Look for dates like "5/5/26" → "2026-05-05"
  - If unclear, set to null

confidence:
  - HIGH = All fields clearly visible, all measurements captured
  - MEDIUM = Some fields slightly unclear but readable
  - LOW = Multiple fields unclear or measurements might be incomplete

unclear_fields:
  - List field names where you're uncertain
  - Examples: ["measurements"], ["operation_number", "judgement"]

═══════════════════════════════════════════════════════════════════
OUTPUT FORMAT:
═══════════════════════════════════════════════════════════════════

Return ONLY a valid JSON array. No markdown. No code blocks. No explanation.
Just the raw JSON array starting with [ and ending with ].

If no data rows found, return: []

REMEMBER: Your success is measured by capturing ALL measurements. Missing measurements = FAILURE."""


def _encode_image(image_path: str) -> str:
    """Encode image to base64 for OpenAI API."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def _call_openai(prompt: str, image_path: str) -> str:
    """Call OpenAI GPT-4o Vision API."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
    
    # Encode image
    base64_image = _encode_image(image_path)
    
    for attempt in range(1, 4):
        try:
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{base64_image}",
                                    "detail": "high"  # High detail for better OCR
                                }
                            }
                        ]
                    }
                ],
                max_tokens=16384,
                temperature=0.0,  # Deterministic
            )
            finish_reason = response.choices[0].finish_reason
            if finish_reason == "length":
                raise RuntimeError(
                    "OpenAI response was truncated before all rows were returned. "
                    "The page was not saved because it may be incomplete."
                )
            return response.choices[0].message.content or ""
        except Exception as exc:
            err = str(exc)
            if "rate_limit" in err.lower() or "429" in err:
                wait = 30 * attempt
                logger.warning("OpenAI rate-limited — waiting %ds (attempt %d)", wait, attempt)
                time.sleep(wait)
            elif "500" in err or "503" in err:
                time.sleep(15 * attempt)
            else:
                raise
    raise RuntimeError("OpenAI: all retries exhausted")


def parse_openai_response(raw: str) -> list[dict]:
    """Parse OpenAI response to extract JSON array."""
    if not raw:
        return []
    
    # Remove markdown code blocks
    text = re.sub(r"```json\s*", "", raw.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```\s*", "", text).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
    except json.JSONDecodeError:
        pass

    # Try to extract JSON array
    start, end = text.find("["), text.rfind("]") + 1
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end])
            if isinstance(parsed, list):
                return [r for r in parsed if isinstance(r, dict)]
        except json.JSONDecodeError:
            pass

    # Try to extract JSON object
    start, end = text.find("{"), text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end])
            if isinstance(parsed, dict):
                return [parsed]
        except json.JSONDecodeError:
            pass

    logger.error("No parseable JSON in OpenAI response: %s", text[:200])
    return []


_JUDGEMENT_MAP = {
    "ok": "OK", "nok": "NOK", "dk": "NOK", "ht": "NOK",
    "na": "NA", "n/a": "NA", "ng": "NOK", "pass": "OK", "fail": "NOK",
}


def _normalise_row(raw: dict, audit_date: Optional[str]) -> dict:
    """Normalize extracted row data."""
    op = str(raw.get("operation_number") or raw.get("op") or "")
    op = op.replace("O", "0").replace("l", "1").replace("I", "1")
    op = re.sub(r"[^\d]", "", op)

    meas = raw.get("measurements") or raw.get("meas")
    if not isinstance(meas, list):
        meas = [meas] if meas is not None else []
    cleaned = []
    for m in meas:
        if m is None:
            continue
        try:
            cleaned.append(float(m))
        except (ValueError, TypeError):
            if isinstance(m, str) and m.strip():
                found = re.findall(r"\d+(?:[.,]\d+)?", m)
                if found:
                    cleaned.extend(float(x.replace(",", ".")) for x in found)
                else:
                    cleaned.append(m.strip())

    raw_j = str(raw.get("judgement") or "").lower().strip()
    return {
        "operation_number": op or None,
        "engine_number": raw.get("engine_number"),
        "process_description": raw.get("process_description") or raw.get("process_name") or raw.get("process") or "",
        "measurements": cleaned,
        "judgement": _JUDGEMENT_MAP.get(raw_j, "UNCLEAR"),
        "audit_date": raw.get("audit_date") or raw.get("date") or audit_date,
        "confidence": raw.get("confidence", "LOW"),
        "unclear_fields": raw.get("unclear_fields") or raw.get("unclear") or [],
    }


def extract_page(image_path: str, audit_date: Optional[str] = None, use_preprocessing: bool = True) -> list[dict]:
    """Extract data from a page image using OpenAI GPT-4o."""
    logger.info("extract_page (OpenAI): %s (preprocessing=%s)", image_path, use_preprocessing)
    
    # Apply preprocessing if enabled
    processed_path = image_path
    if use_preprocessing:
        from services.image_preprocessor import preprocess_image
        try:
            processed_path = preprocess_image(image_path, enhance_handwriting=True)
            logger.info("Using preprocessed image: %s", processed_path)
        except Exception as e:
            logger.warning("Preprocessing failed, using original: %s", e)
            processed_path = image_path
    
    raw = _call_openai(_EXTRACTION_PROMPT, processed_path)
    items = parse_openai_response(raw)
    rows = [_normalise_row(r, audit_date) for r in items]
    
    # Log measurement counts for verification
    for i, row in enumerate(rows):
        meas_count = len(row.get("measurements", []))
        logger.info("Row %d (Op %s): %d measurements extracted", 
                   i+1, row.get("operation_number", "?"), meas_count)
    
    logger.info("Extracted %d rows from %s", len(rows), image_path)
    return rows


def extract_from_image(image_path: str) -> dict:
    """Interface used by upload.py - compatible with Gemini version."""
    try:
        rows = extract_page(image_path)
    except Exception as exc:
        logger.error("OCR failed for %s: %s", image_path, exc)
        return {"audit_date": None, "sheet_type": "TORQUE", "rows": [], "raw_response": "", "error": str(exc)}

    sheet_date = next((r["audit_date"] for r in rows if r.get("audit_date")), None)
    mapped = []
    for i, r in enumerate(rows):
        op = str(r.get("operation_number") or "").strip()
        if not op or not op.isdigit():
            continue
        judgement = r.get("judgement", "UNCLEAR")
        if judgement not in ("OK", "NOK", "DK", "HT"):
            continue
        if judgement in ("DK", "HT"):
            judgement = "NOK"
        mapped.append({
            "operation_number": op,
            "process_name": r.get("process_description") or "",
            "measurements": r.get("measurements") or [],
            "judgement": judgement,
            "audit_date": r.get("audit_date") or sheet_date,
            "y": float(i),
        })

    logger.info("extract_from_image: %d rows saved (of %d extracted)", len(mapped), len(rows))
    return {
        "audit_date": sheet_date,
        "sheet_type": "TORQUE",
        "rows": mapped,
        "raw_response": "",
        "error": None,
    }
