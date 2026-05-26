"""
Analytics router.

CRITICAL INVARIANT: Every query in this file filters on
    review_status = 'APPROVED'
Analytics results MUST NEVER include unverified OCR data.

Endpoints:
  GET /operations                         — list distinct approved operations
  GET /analytics?operation_number=&start_date=&end_date=
                                          — historical data for one operation
"""

import logging
import math
import re
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from database import get_db
from models import ExtractedOperation, StandardTemplateRow, Upload
from services.standard_template import _clean_op, _parse_limits

logger = logging.getLogger(__name__)
router = APIRouter()

_APPROVED = "APPROVED"
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _date_from_filename(filename: str | None, fallback_year: int) -> date | None:
    if not filename:
        return None
    match = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s*[-_ ]*\s*"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
        r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
        filename,
        flags=re.I,
    )
    if not match:
        return None
    day = int(match.group(1))
    month = _MONTHS.get(match.group(2).lower())
    if not month:
        return None
    try:
        return date(fallback_year, month, day)
    except ValueError:
        return None


def _display_date(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _template_limits_for_row(row: ExtractedOperation, db: Session) -> tuple[float | None, float | None, float | None]:
    op_key = _clean_op(row.operation_number)
    if not op_key:
        return None, None, None

    templates = db.query(StandardTemplateRow).all()
    matches = [
        template for template in templates
        if _clean_op(template.operation_number) == op_key
    ]
    if row.process_name:
        process = row.process_name.strip()
        process_matches = [
            template for template in matches
            if (template.process_name or "").strip() == process
        ]
        if process_matches:
            matches = process_matches

    for template in matches:
        candidates = []
        for text in (template.tightening_torque, template.engineering_spec):
            nominal, lower, upper = _parse_limits(text)
            score = int(lower is not None) + int(upper is not None)
            if score:
                candidates.append((score, nominal, lower, upper))
        if candidates:
            _score, nominal, lower, upper = max(candidates, key=lambda item: item[0])
        else:
            nominal, lower, upper = _parse_limits(template.engineering_spec or template.tightening_torque)
        if nominal is not None or lower is not None or upper is not None:
            return nominal, lower, upper
    return None, None, None


@router.get("/operations")
def list_operations(db: Session = Depends(get_db)):
    """
    Return one entry per distinct operation_number (APPROVED rows only).
    Uses the most frequently occurring process_name for that operation.
    """
    from sqlalchemy import case, text

    # Aggregate by operation_number only
    agg = (
        db.query(
            ExtractedOperation.operation_number,
            func.count(ExtractedOperation.id).label("approved_count"),
            func.sum(
                case((ExtractedOperation.judgement.in_(["NOK", "NG"]), 1), else_=0)
            ).label("nok_count"),
            func.max(ExtractedOperation.audit_date).label("last_audit_date"),
        )
        .filter(ExtractedOperation.review_status == _APPROVED)
        .filter(ExtractedOperation.operation_number.isnot(None))
        .group_by(ExtractedOperation.operation_number)
        .order_by(ExtractedOperation.operation_number)
        .all()
    )

    # For each operation_number, pick the most common process_name
    name_rows = (
        db.query(
            ExtractedOperation.operation_number,
            ExtractedOperation.process_name,
            func.count(ExtractedOperation.id).label("cnt"),
        )
        .filter(ExtractedOperation.review_status == _APPROVED)
        .filter(ExtractedOperation.operation_number.isnot(None))
        .filter(ExtractedOperation.process_name.isnot(None))
        .group_by(ExtractedOperation.operation_number, ExtractedOperation.process_name)
        .all()
    )

    # Build map: operation_number → most frequent process_name
    best_name: dict[str, str] = {}
    best_cnt: dict[str, int] = {}
    for row in name_rows:
        op = row.operation_number
        if row.cnt > best_cnt.get(op, 0):
            best_name[op] = row.process_name
            best_cnt[op] = row.cnt

    return [
        {
            "operation_number": r.operation_number,
            "process_name": best_name.get(r.operation_number),
            "approved_count": r.approved_count,
            "nok_count": int(r.nok_count or 0),
            "last_audit_date": r.last_audit_date.isoformat() if r.last_audit_date else None,
        }
        for r in agg
    ]


@router.get("/analytics")
def get_analytics(
    operation_number: str = Query(..., description="Operation number to analyse"),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """
    Return historical measurement rows for a single operation.

    Only APPROVED rows are returned — this is enforced unconditionally.
    """
    query = (
        db.query(ExtractedOperation)
        .filter(ExtractedOperation.review_status == _APPROVED)
        .filter(ExtractedOperation.operation_number == operation_number)
    )

    if start_date:
        try:
            sd = date.fromisoformat(start_date)
            query = query.filter(ExtractedOperation.audit_date >= sd)
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date must be YYYY-MM-DD")

    if end_date:
        try:
            ed = date.fromisoformat(end_date)
            query = query.filter(ExtractedOperation.audit_date <= ed)
        except ValueError:
            raise HTTPException(status_code=400, detail="end_date must be YYYY-MM-DD")

    rows = query.order_by(ExtractedOperation.audit_date.asc(), ExtractedOperation.id.asc()).all()
    rows = _dedupe_operation_rows(rows)

    if not rows:
        # Return empty analytics structure — not an error
        return {
            "operation_number": operation_number,
            "process_name": None,
            "rows": [],
            "stats": {
                "total": 0,
                "ok_count": 0,
                "nok_count": 0,
                "ok_pct": 0,
                "nok_pct": 0,
                "avg_torque": None,
                "cp": None,
                "cpk": None,
            },
        }

    process_name = rows[0].process_name

    serialised_rows = []
    ok_count = 0
    nok_count = 0
    all_measurements: list[float] = []
    lower_limits: list[float] = []
    upper_limits: list[float] = []
    upload_ids = [row.upload_id for row in rows if row.upload_id]
    uploads = {
        upload.id: upload
        for upload in db.query(Upload).filter(Upload.id.in_(upload_ids)).all()
    } if upload_ids else {}

    for r in rows:
        measurements = r.measurements_json or []
        judgement = (r.judgement or "").upper()
        row_data = (r.raw_ocr_json or {}).get("row_data", {}) if isinstance(r.raw_ocr_json, dict) else {}
        upload = uploads.get(r.upload_id)
        year = (
            r.audit_date.year
            if r.audit_date
            else upload.created_at.year
            if upload and upload.created_at
            else date.today().year
        )
        display_date = _date_from_filename(upload.original_filename if upload else None, year) or r.audit_date
        nominal = row_data.get("nominal")
        lower_limit = row_data.get("lower_limit")
        upper_limit = row_data.get("upper_limit")
        if nominal is None or lower_limit is None or upper_limit is None:
            template_nominal, template_lower, template_upper = _template_limits_for_row(r, db)
            nominal = nominal if nominal is not None else template_nominal
            lower_limit = lower_limit if lower_limit is not None else template_lower
            upper_limit = upper_limit if upper_limit is not None else template_upper

        if judgement == "OK":
            ok_count += 1
        elif judgement in {"NOK", "NG"}:
            nok_count += 1

        for m in measurements:
            try:
                all_measurements.append(float(m))
            except (TypeError, ValueError):
                pass
        try:
            if lower_limit is not None:
                lower_limits.append(float(lower_limit))
            if upper_limit is not None:
                upper_limits.append(float(upper_limit))
        except (TypeError, ValueError):
            pass

        serialised_rows.append(
            {
                "id": r.id,
                "audit_date": _display_date(display_date),
                "column_key": str(r.upload_id) if r.upload_id else _display_date(display_date),
                "column_label": _display_date(display_date),
                "upload_id": str(r.upload_id) if r.upload_id else None,
                "upload_filename": upload.original_filename if upload else None,
                "measurements": measurements,
                "judgement": r.judgement,
                "nominal": nominal,
                "upper_limit": upper_limit,
                "lower_limit": lower_limit,
            }
        )

    total = len(rows)
    ok_pct = round(ok_count / total * 100, 1) if total else 0
    nok_pct = round(nok_count / total * 100, 1) if total else 0
    avg_torque = round(sum(all_measurements) / len(all_measurements), 2) if all_measurements else None
    min_torque = round(min(all_measurements), 2) if all_measurements else None
    max_torque = round(max(all_measurements), 2) if all_measurements else None
    cp, cpk = _capability_indices(all_measurements, lower_limits, upper_limits)

    return {
        "operation_number": operation_number,
        "process_name": process_name,
        "rows": serialised_rows,
        "stats": {
            "total": total,
            "ok_count": ok_count,
            "nok_count": nok_count,
            "ok_pct": ok_pct,
            "nok_pct": nok_pct,
            "avg_torque": avg_torque,
            "min_torque": min_torque,
            "max_torque": max_torque,
            "cp": cp,
            "cpk": cpk,
        },
    }


def _capability_indices(
    values: list[float],
    lower_limits: list[float],
    upper_limits: list[float],
) -> tuple[float | None, float | None]:
    if len(values) < 2 or not lower_limits or not upper_limits:
        return None, None

    lower = min(lower_limits)
    upper = max(upper_limits)
    if upper <= lower:
        return None, None

    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    sigma = math.sqrt(variance)
    if sigma <= 0:
        return None, None

    cp = (upper - lower) / (6 * sigma)
    cpk = min((upper - mean) / (3 * sigma), (mean - lower) / (3 * sigma))
    return round(cp, 2), round(cpk, 2)


def _dedupe_operation_rows(rows: list[ExtractedOperation]) -> list[ExtractedOperation]:
    """
    Keep one approved row per operation/date/upload.

    OCR can occasionally duplicate an operation number on nearby rows from the
    same uploaded sheet. For analytics, the row with the fullest measurement
    vector is the canonical row for that operation/date.
    """
    best_by_group: dict[tuple[str | None, date | None, str | None], ExtractedOperation] = {}

    for row in rows:
        key = (
            row.operation_number,
            row.audit_date,
            str(row.upload_id) if row.upload_id else None,
        )
        current = best_by_group.get(key)
        if current is None:
            best_by_group[key] = row
            continue

        current_count = len(current.measurements_json or [])
        next_count = len(row.measurements_json or [])
        if next_count > current_count:
            best_by_group[key] = row

    return sorted(best_by_group.values(), key=lambda row: (row.audit_date or date.min, row.id))
