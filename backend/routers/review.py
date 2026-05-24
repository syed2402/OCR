"""
Review router — human-in-the-loop data verification.

Endpoints:
  GET  /uploads/{upload_id}/rows          — list all extracted rows for a session
  GET  /rows/{id}                         — fetch a single row
  PUT  /review-row/{id}                   — save corrected values (→ REVIEWED)
  POST /approve-row/{id}                  — mark approved (→ APPROVED)
  POST /reject-row/{id}                   — mark rejected (→ REJECTED)

IMPORTANT: Only APPROVED rows are used by the analytics engine.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import ExtractedOperation

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ReviewRowPayload(BaseModel):
    operation_number: Optional[str] = None
    engine_number: Optional[str] = None
    process_name: Optional[str] = None
    quantity: Optional[int] = None
    audit_date: Optional[str] = None  # YYYY-MM-DD
    measurements: Optional[list[float]] = None
    judgement: Optional[str] = None


def _accuracy_score(value) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    score = float(value)
    if score > 1:
        score = score / 100
    return round(max(0, min(1, score)), 2)


def _normalise_accuracy_scores(scores) -> dict:
    if not isinstance(scores, dict):
        return {}

    normalised = {}
    for key, value in scores.items():
        if isinstance(value, list):
            normalised[key] = [
                score for item in value
                if (score := _accuracy_score(item)) is not None
            ]
        else:
            score = _accuracy_score(value)
            if score is not None:
                normalised[key] = score
    return normalised


def _quantity_from_row(row: ExtractedOperation) -> int | None:
    if row.quantity is not None:
        return row.quantity
    if isinstance(row.raw_ocr_json, dict):
        row_data = row.raw_ocr_json.get("row_data")
        if isinstance(row_data, dict):
            value = row_data.get("quantity") or row_data.get("qty")
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass
    measurements = row.measurements_json or []
    if measurements:
        return len(_display_measurements(measurements, None))
    return None


def _validated_measurements(values, quantity: int | None, require_exact: bool = False) -> list[float]:
    measurements = []
    for value in values or []:
        try:
            measurements.append(float(value))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Machine values must be numeric.")
    if quantity is not None and quantity >= 0 and len(measurements) > quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Quantity is {quantity}; only {quantity} machine values are allowed.",
        )
    if require_exact and quantity is not None and quantity >= 0 and len(measurements) != quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Quantity is {quantity}; exactly {quantity} numeric machine values are required before approval.",
        )
    return measurements


def _display_measurements(values, quantity: int | None) -> list[float]:
    measurements = []
    for value in values or []:
        try:
            measurements.append(float(value))
        except (TypeError, ValueError):
            continue
    return measurements


def _row_page(row: ExtractedOperation) -> int | None:
    if isinstance(row.raw_ocr_json, dict):
        page = row.raw_ocr_json.get("page")
        try:
            return int(page) if page is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _is_real_operation_number(value) -> bool:
    text = str(value or "").strip()
    if not text or text in {"-", "—", "_"}:
        return False
    return any(char.isdigit() for char in text)


def _looks_like_continuation(row: ExtractedOperation) -> bool:
    if _is_real_operation_number(row.operation_number):
        return False
    return bool(_display_measurements(row.measurements_json, None))


def _same_wrapped_quantity_row(current: ExtractedOperation, row: ExtractedOperation) -> bool:
    if _row_page(current) != _row_page(row):
        return False
    if not _is_real_operation_number(row.operation_number):
        return False
    if str(row.operation_number).strip() != str(current.operation_number).strip():
        return False
    current_quantity = _quantity_from_row(current)
    row_quantity = _quantity_from_row(row)
    if current_quantity is None or row_quantity is None or current_quantity != row_quantity:
        return False
    if current_quantity <= 6:
        return False
    if (current.process_name or "").strip() != (row.process_name or "").strip():
        return False
    current_count = len(_display_measurements(current.measurements_json, None))
    row_count = len(_display_measurements(row.measurements_json, None))
    return current_count < current_quantity and current_count + row_count <= current_quantity


def _repair_wrapped_rows(rows: list[ExtractedOperation], db: Session) -> None:
    changed = False
    current: ExtractedOperation | None = None

    for row in rows:
        if _is_real_operation_number(row.operation_number):
            if current is not None and _same_wrapped_quantity_row(current, row):
                merged = _display_measurements(current.measurements_json, None)
                merged.extend(_display_measurements(row.measurements_json, None))
                current.measurements_json = merged
                if not current.engine_number and row.engine_number:
                    current.engine_number = row.engine_number
                db.delete(row)
                changed = True
                continue

            current = row
            measurements = _display_measurements(row.measurements_json, None)
            quantity = _quantity_from_row(row)
            if measurements and quantity is None:
                row.quantity = len(measurements)
                changed = True
            continue

        if current is None or _row_page(current) != _row_page(row) or not _looks_like_continuation(row):
            continue

        if row.quantity is not None:
            row.operation_number = current.operation_number
            if not row.engine_number:
                row.engine_number = current.engine_number
            if not row.process_name:
                row.process_name = current.process_name
            current = row
            changed = True
            continue

        merged = _display_measurements(current.measurements_json, None)
        merged.extend(_display_measurements(row.measurements_json, None))
        current.measurements_json = merged
        if current.quantity is None:
            current.quantity = len(merged)

        if not current.engine_number and row.engine_number:
            current.engine_number = row.engine_number
        if not current.process_name and row.process_name:
            current.process_name = row.process_name
        db.delete(row)
        changed = True

    if changed:
        db.commit()


def _row_to_dict(row: ExtractedOperation) -> dict:
    page = None
    engine_number = row.engine_number
    confidence_scores = {}
    unclear_fields = []
    if isinstance(row.raw_ocr_json, dict):
        page = row.raw_ocr_json.get("page")
        row_data = row.raw_ocr_json.get("row_data")
        if isinstance(row_data, dict):
            engine_number = engine_number or row_data.get("engine_number")
            confidence_scores = row_data.get("confidence_scores") or {}
            unclear_fields = row_data.get("unclear_fields") or []
        confidence_scores = row.raw_ocr_json.get("confidence_scores") or confidence_scores
        unclear_fields = row.raw_ocr_json.get("unclear_fields") or unclear_fields
    confidence_scores = _normalise_accuracy_scores(confidence_scores)
    quantity = _quantity_from_row(row)
    return {
        "id": row.id,
        "upload_id": str(row.upload_id) if row.upload_id else None,
        "audit_date": row.audit_date.isoformat() if row.audit_date else None,
        "operation_number": row.operation_number,
        "engine_number": engine_number,
        "process_name": row.process_name,
        "quantity": quantity,
        "judgement": row.judgement,
        "measurements": _display_measurements(row.measurements_json, quantity),
        "corrected": row.corrected_json,
        "review_status": row.review_status,
        "row_image_path": row.row_image_path,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "page": page,
        "gemini_raw": row.gemini_raw,
        "gpt4o_raw": row.gpt4o_raw,
        "agreement_score": row.agreement_score,
        "disagreements": row.disagreements or [],
        "confidence_scores": confidence_scores,
        "unclear_fields": unclear_fields,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/uploads/{upload_id}/rows")
def get_upload_rows(upload_id: str, db: Session = Depends(get_db)):
    """Return all rows extracted from a given upload, ordered by page and operation."""
    from sqlalchemy import text as _text
    rows = (
        db.query(ExtractedOperation)
        .filter(ExtractedOperation.upload_id == upload_id)
        .order_by(
            # Sort by page number first (stored in raw_ocr_json->>'page')
            _text("(raw_ocr_json->>'page')::int ASC NULLS LAST"),
            _text("NULLIF(regexp_replace(operation_number, '\\D', '', 'g'), '')::int ASC NULLS LAST"),
            ExtractedOperation.id.asc(),
        )
        .all()
    )
    _repair_wrapped_rows(rows, db)
    rows = (
        db.query(ExtractedOperation)
        .filter(ExtractedOperation.upload_id == upload_id)
        .order_by(
            _text("(raw_ocr_json->>'page')::int ASC NULLS LAST"),
            _text("NULLIF(regexp_replace(operation_number, '\\D', '', 'g'), '')::int ASC NULLS LAST"),
            ExtractedOperation.id.asc(),
        )
        .all()
    )
    return [_row_to_dict(r) for r in rows]


@router.get("/rows/{row_id}")
def get_row(row_id: int, db: Session = Depends(get_db)):
    row = db.query(ExtractedOperation).filter(ExtractedOperation.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")
    return _row_to_dict(row)


@router.put("/review-row/{row_id}")
def review_row(row_id: int, payload: ReviewRowPayload, db: Session = Depends(get_db)):
    """
    Save user corrections to a row.
    Transitions status: EXTRACTED → REVIEWED (or stays REVIEWED if already corrected).
    """
    row = db.query(ExtractedOperation).filter(ExtractedOperation.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    was_approved = row.review_status == "APPROVED"

    # Apply corrections
    if payload.operation_number is not None:
        row.operation_number = payload.operation_number
    if payload.engine_number is not None:
        row.engine_number = payload.engine_number
    if payload.process_name is not None:
        row.process_name = payload.process_name
    if payload.quantity is not None:
        if payload.quantity < 0:
            raise HTTPException(status_code=400, detail="quantity must be zero or greater")
        row.quantity = payload.quantity
    if payload.measurements is not None:
        row.measurements_json = _validated_measurements(payload.measurements, row.quantity)
    if payload.judgement is not None:
        row.judgement = payload.judgement
    if payload.audit_date is not None:
        try:
            from datetime import date
            row.audit_date = datetime.strptime(payload.audit_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="audit_date must be YYYY-MM-DD")

    # Store corrected snapshot
    row.corrected_json = {
        "operation_number": row.operation_number,
        "engine_number": row.engine_number,
        "process_name": row.process_name,
        "quantity": row.quantity,
        "measurements": row.measurements_json,
        "judgement": row.judgement,
        "audit_date": row.audit_date.isoformat() if row.audit_date else None,
    }
    row.review_status = "APPROVED" if was_approved else "REVIEWED"
    row.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


@router.post("/approve-row/{row_id}")
def approve_row(row_id: int, db: Session = Depends(get_db)):
    """
    Approve a row for analytics consumption.
    Only APPROVED rows are visible to the analytics engine.
    """
    row = db.query(ExtractedOperation).filter(ExtractedOperation.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    row.measurements_json = _validated_measurements(row.measurements_json or [], _quantity_from_row(row))
    row.review_status = "APPROVED"
    row.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


@router.post("/reject-row/{row_id}")
def reject_row(row_id: int, db: Session = Depends(get_db)):
    """Mark a row as rejected — it will never appear in analytics."""
    row = db.query(ExtractedOperation).filter(ExtractedOperation.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    row.review_status = "REJECTED"
    row.reviewed_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)


@router.post("/uploads/{upload_id}/approve-all")
def approve_all(upload_id: str, db: Session = Depends(get_db)):
    """Bulk-approve all EXTRACTED/REVIEWED rows for an upload in one call."""
    rows = (
        db.query(ExtractedOperation)
        .filter(
            ExtractedOperation.upload_id == upload_id,
            ExtractedOperation.review_status.in_(["EXTRACTED", "REVIEWED"]),
        )
        .all()
    )
    for row in rows:
        _validated_measurements(row.measurements_json or [], _quantity_from_row(row))
    for row in rows:
        row.review_status = "APPROVED"
        row.reviewed_at = datetime.utcnow()
    db.commit()
    return {"approved": len(rows)}


@router.post("/unapprove-row/{row_id}")
def unapprove_row(row_id: int, db: Session = Depends(get_db)):
    """Revert an approved row back to REVIEWED for re-editing."""
    row = db.query(ExtractedOperation).filter(ExtractedOperation.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    row.review_status = "REVIEWED"
    db.commit()
    db.refresh(row)
    return _row_to_dict(row)
