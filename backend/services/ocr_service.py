"""
Stellantis OCR extraction — Gemini 2.5 Flash.

Sends each page image to Gemini, parses JSON rows, validates, returns.
"""

import json
import logging
import os
import re
import time
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from google import genai as google_genai
from google.genai import types as google_types
from PIL import Image

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"
# Cap longest side — keeps handwriting readable, cuts API upload time
MAX_IMAGE_PX = 4096

_EXTRACTION_PROMPT = """Extract ALL data rows from this Stellantis TORQUE AUDIT SHEET.

CRITICAL: Extract EVERY measurement value. Missing measurements = FAILURE.
CRITICAL: Keep each measurement row attached to the operation code printed on the SAME horizontal band. Never shift values to the next operation code.

Return ONLY a minified JSON array. Use this compact schema per row:
{"op":"1070","engine_number":"105925","process":"MB Cap removal & PCN","quantity":3,"nominal":20,"upper_limit":30,"lower_limit":20,"measurements":[21.20,18.20,19.40],"judgement":"OK","date":"2026-05-05","confidence":{"op":0.95,"engine_number":0.92,"process":0.90,"quantity":0.92,"measurements":[0.88,0.82,0.91],"judgement":0.92,"date":0.96},"unclear":[]}

RULES:
1. op: 4 digits. Fix OCR errors: O->0, l->1, I->1, S->5, Z->2, B->8
   - The operation code is in the far-left column.
   - It may span multiple visual sub-rows. All actual values inside that vertical span belong to that same op.
   - Do NOT assign continuation values to the next visible operation code below.
2. engine_number: extract the handwritten/stamped engine number from the row if visible. Use null if not visible.
3. nominal, upper_limit, lower_limit: extract printed specification/tolerance values for the row from the PDF if visible.
   - If the sheet shows a target/nominal torque, return it as nominal.
   - If the sheet shows final min/max limits, return those as lower_limit and upper_limit.
   - If the sheet shows tolerance values around nominal, convert them to final lower/upper limits when possible.
   - Example: nominal 20 with +10/-0 tolerance means lower_limit=20 and upper_limit=30.
   - Use null when not visible.
4. quantity: extract the row quantity / sample count from the PDF if visible. It is the expected number of machine values for that row. Use null when not visible.
5. measurements: extract ONLY handwritten numeric machine values in the Actual/measurement columns.
   - Preserve decimal points exactly. Examples: read 21.20 as 21.20, 18.20 as 18.20, 19.40 as 19.40.
   - Do NOT round decimals to whole numbers.
   - Do NOT split one decimal value into multiple values.
   - Never return text in measurements; every measurement must be a JSON number.
   - Do NOT repeat values to fill table cells.
   - Ignore blank cells, crossed/X cells, printed tolerance text, and printed equipment ranges.
   - If quantity is more than the visible Actual columns in one printed line, continue reading the wrapped/next visual line for the same operation row.
   - Do not create a separate JSON row for continuation values; append them to the same measurements array.
   - Example: if op 1250 visually spans four sub-rows, all numbers in those four sub-rows belong to op 1250, even if op 1290 appears below.
   - Do NOT move 1250 continuation values into the 1290 row.
   - Before returning JSON, cross-check every measurements array against the left operation-code column.
   - Scan left-to-right across the row and return only the visible handwritten measurement entries.
   - If a row has 3 handwritten measurements, return 3 values, not 6.
   - If quantity is 8, return exactly 8 measurement values when all 8 are visible, even if they wrap to a second visual line.
   - If quantity is 3, return exactly 3 measurement values when all 3 are visible.
   - If handwriting is unclear, do not guess. Omit that value and add "measurements" to unclear.
   - Prefer an incomplete measurements array over a guessed wrong number.
6. judgement: OK/NOK/DK/HT/NA. Normalize Pass->OK, Fail->NOK, NG->NOK
7. date: From header, format YYYY-MM-DD. Use null if unclear.
8. confidence: numeric 0-1 confidence for every extracted value.
   - op/process/judgement/date are single numbers.
   - quantity is a single number.
   - measurements must be an array with one confidence score per measurement value.
   - Use 0.90-1.00 only when clearly readable, 0.70-0.89 for mostly readable, below 0.70 when uncertain.
9. unclear: array of field names you could not read confidently, for example ["measurements"].

Return ONLY valid JSON array. No markdown. No explanation.
Empty result: []"""


@lru_cache(maxsize=1)
def _get_client() -> google_genai.Client:
    return google_genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))


def _prepare_for_api(pil_image: Image.Image) -> Image.Image:
    """Downscale oversized scans — faster upload, no accuracy loss on audit sheets."""
    w, h = pil_image.size
    longest = max(w, h)
    if longest <= MAX_IMAGE_PX:
        return pil_image
    ratio = MAX_IMAGE_PX / longest
    new_size = (int(w * ratio), int(h * ratio))
    return pil_image.resize(new_size, Image.Resampling.LANCZOS)


def _call_gemini(prompt: str, pil_image: Image.Image) -> str:
    client = _get_client()
    pil_image = _prepare_for_api(pil_image)

    for attempt in range(1, 4):
        try:
            resp = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[prompt, pil_image],
                config=google_types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=16384,
                    thinking_config=google_types.ThinkingConfig(thinking_budget=0),
                ),
            )
            
            # Check if response was truncated
            if hasattr(resp, 'candidates') and resp.candidates:
                candidate = resp.candidates[0]
                if hasattr(candidate, 'finish_reason'):
                    logger.info(f"Finish reason: {candidate.finish_reason}")
                    if 'MAX_TOKENS' in str(candidate.finish_reason):
                        logger.warning("Response was truncated due to max_output_tokens limit!")
                        raise RuntimeError(
                            "Gemini response was truncated before all rows were returned. "
                            "The page was not saved because it may be incomplete."
                        )
            
            return resp.text or ""
        except Exception as exc:
            err = str(exc)
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                m = re.search(r"retry in (\d+(?:\.\d+)?)s", err)
                wait = min(float(m.group(1)) + 3 if m else 20, 25)
                logger.warning("Gemini rate-limited — waiting %.0fs (attempt %d)", wait, attempt)
                time.sleep(wait)
            elif any(c in err for c in ("503", "500", "overloaded")):
                time.sleep(10 * attempt)
            else:
                raise
    raise RuntimeError("Gemini: all retries exhausted")


def parse_gemini_response(raw: str) -> list[dict]:
    if not raw:
        return []
    
    # Log the raw response length for debugging
    logger.info(f"Raw response length: {len(raw)} characters")
    
    text = re.sub(r"```json\s*", "", raw.strip(), flags=re.IGNORECASE)
    text = re.sub(r"```\s*", "", text).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return [parsed]
        if isinstance(parsed, list):
            return [r for r in parsed if isinstance(r, dict)]
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error: {e}")
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

    logger.error("No parseable JSON in Gemini response: %s", text[:500])
    logger.error("Response ends with: %s", text[-200:] if len(text) > 200 else text)
    return []


_JUDGEMENT_MAP = {
    "ok": "OK", "nok": "NOK", "dk": "NOK", "ht": "NOK",
    "na": "NA", "n/a": "NA", "ng": "NOK", "pass": "OK", "fail": "NOK",
}


def _confidence_value(value, default: float = 0.75) -> float:
    if isinstance(value, (int, float)):
        score = float(value)
        if score > 1:
            score = score / 100
        return round(max(0, min(1, score)), 2)
    label = str(value or "").strip().upper()
    if label == "HIGH":
        return 0.92
    if label in {"MED", "MEDIUM"}:
        return 0.78
    if label == "LOW":
        return 0.55
    return default


def _normalise_confidence(raw: dict, measurement_count: int, unclear_fields: list) -> dict:
    confidence = raw.get("confidence")
    if not isinstance(confidence, dict):
        confidence = {}

    row_default = _confidence_value(confidence if confidence else raw.get("confidence"), 75)
    low_fields = set(str(field) for field in unclear_fields or [])

    def field_score(*names: str) -> int:
      for name in names:
          if name in confidence:
              return _confidence_value(confidence.get(name), row_default)
      return 55 if any(name in low_fields for name in names) else row_default

    measurement_scores = confidence.get("measurements")
    if not isinstance(measurement_scores, list):
        measurement_scores = []
    measurement_scores = [
        _confidence_value(measurement_scores[i], field_score("measurements"))
        if i < len(measurement_scores)
        else field_score("measurements")
        for i in range(measurement_count)
    ]

    return {
        "operation_number": field_score("operation_number", "op"),
        "engine_number": field_score("engine_number"),
        "process_name": field_score("process_name", "process", "process_description"),
        "quantity": field_score("quantity", "qty", "sample_count"),
        "measurements": measurement_scores,
        "judgement": field_score("judgement"),
        "audit_date": field_score("audit_date", "date"),
    }


def _number_or_none(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        if isinstance(value, str):
            found = re.search(r"-?\d+(?:[.,]\d+)?", value)
            if found:
                return float(found.group(0).replace(",", "."))
    return None


def _normalise_row(raw: dict, audit_date: Optional[str]) -> dict:
    op = str(raw.get("operation_number") or raw.get("op") or "")
    op = op.replace("O", "0").replace("l", "1").replace("I", "1")
    op = re.sub(r"[^\d]", "", op)

    quantity = _number_or_none(raw.get("quantity") or raw.get("qty") or raw.get("sample_count"))
    quantity = int(quantity) if quantity is not None else None

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
    if quantity is not None:
        cleaned = cleaned[:max(0, quantity)]

    raw_j = str(raw.get("judgement") or "").lower().strip()
    unclear_fields = raw.get("unclear_fields") or raw.get("unclear") or []
    return {
        "operation_number": op or None,
        "engine_number": raw.get("engine_number"),
        "process_description": raw.get("process_description") or raw.get("process_name") or raw.get("process") or "",
        "quantity": quantity,
        "nominal": _number_or_none(raw.get("nominal") or raw.get("target") or raw.get("spec")),
        "upper_limit": _number_or_none(raw.get("upper_limit") or raw.get("upper") or raw.get("max")),
        "lower_limit": _number_or_none(raw.get("lower_limit") or raw.get("lower") or raw.get("min")),
        "measurements": cleaned,
        "judgement": _JUDGEMENT_MAP.get(raw_j, "UNCLEAR"),
        "audit_date": raw.get("audit_date") or raw.get("date") or audit_date,
        "confidence": raw.get("confidence", "LOW"),
        "confidence_scores": _normalise_confidence(raw, len(cleaned), unclear_fields),
        "unclear_fields": unclear_fields,
    }


def _merge_continuation_rows(rows: list[dict]) -> list[dict]:
    merged: list[dict] = []
    current: dict | None = None

    for row in rows:
        op = str(row.get("operation_number") or "").strip()
        measurements = row.get("measurements") or []

        if op:
            current = row
            merged.append(row)
        elif current is not None and measurements:
            current_measurements = current.setdefault("measurements", [])
            current_measurements.extend(measurements)

            current_scores = current.setdefault("confidence_scores", {})
            row_scores = row.get("confidence_scores") or {}
            current_measurement_scores = current_scores.setdefault("measurements", [])
            current_measurement_scores.extend(row_scores.get("measurements") or [])

            current_unclear = set(current.get("unclear_fields") or [])
            current_unclear.update(row.get("unclear_fields") or [])
            current["unclear_fields"] = sorted(current_unclear)

            current_quantity = current.get("quantity")
            if current_quantity is None or current_quantity < len(current_measurements):
                current["quantity"] = len(current_measurements)

    for row in merged:
        measurements = row.get("measurements") or []
        quantity = row.get("quantity")
        if quantity is None or quantity < len(measurements):
            row["quantity"] = len(measurements) if measurements else quantity

    return merged


def extract_page(image_path: str, audit_date: Optional[str] = None) -> list[dict]:
    logger.info("extract_page: %s", image_path)
    pil_image = Image.open(image_path).convert("RGB")
    raw = _call_gemini(_EXTRACTION_PROMPT, pil_image)
    items = parse_gemini_response(raw)
    rows = _merge_continuation_rows([_normalise_row(r, audit_date) for r in items])

    for i, row in enumerate(rows):
        meas_count = len(row.get("measurements", []))
        logger.info(
            "Row %d (Op %s): %d measurements",
            i + 1, row.get("operation_number", "?"), meas_count,
        )

    logger.info("Extracted %d rows from %s", len(rows), image_path)
    return rows


def extract_from_image(image_path: str) -> dict:
    """Interface used by upload.py."""
    try:
        rows = extract_page(image_path)
    except Exception as exc:
        logger.error("OCR failed for %s: %s", image_path, exc)
        return {"audit_date": None, "sheet_type": "TORQUE", "rows": [], "raw_response": "", "error": str(exc)}

    sheet_date = next((r["audit_date"] for r in rows if r.get("audit_date")), None)
    mapped = []
    for i, r in enumerate(rows):
        op = str(r.get("operation_number") or "").strip()
        
        # Skip rows without operation number
        if not op:
            logger.debug("Skipping row %d: no operation number", i)
            continue
            
        # Allow operation numbers with non-digits (will be cleaned later)
        # Just check if it has at least some digits
        if not any(c.isdigit() for c in op):
            logger.debug("Skipping row %d: operation number has no digits: %s", i, op)
            continue
        
        judgement = r.get("judgement", "UNCLEAR")
        
        # Be more lenient with judgement - accept more values
        if judgement not in ("OK", "NOK", "DK", "HT", "NA", "UNCLEAR"):
            logger.warning("Row %d: Unknown judgement '%s', defaulting to UNCLEAR", i, judgement)
            judgement = "UNCLEAR"
            
        if judgement in ("DK", "HT"):
            judgement = "NOK"
            
        mapped.append({
            "operation_number": op,
            "engine_number": r.get("engine_number"),
            "process_name": r.get("process_description") or "",
            "quantity": r.get("quantity"),
            "nominal": r.get("nominal"),
            "upper_limit": r.get("upper_limit"),
            "lower_limit": r.get("lower_limit"),
            "measurements": r.get("measurements") or [],
            "judgement": judgement,
            "audit_date": r.get("audit_date") or sheet_date,
            "confidence_scores": r.get("confidence_scores") or {},
            "unclear_fields": r.get("unclear_fields") or [],
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
