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
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from database import get_db
from models import ExtractedOperation

logger = logging.getLogger(__name__)
router = APIRouter()

_APPROVED = "APPROVED"


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

    rows = query.order_by(ExtractedOperation.audit_date.asc()).all()

    if not rows:
        # Return empty analytics structure — not an error
        return {
            "operation_number": operation_number,
            "process_name": None,
            "rows": [],
            "stats": {"total": 0, "ok_count": 0, "nok_count": 0, "ok_pct": 0, "nok_pct": 0, "avg_torque": None},
        }

    process_name = rows[0].process_name

    serialised_rows = []
    ok_count = 0
    nok_count = 0
    all_measurements: list[float] = []

    for r in rows:
        measurements = r.measurements_json or []
        judgement = (r.judgement or "").upper()

        if judgement == "OK":
            ok_count += 1
        elif judgement in {"NOK", "NG"}:
            nok_count += 1

        for m in measurements:
            try:
                all_measurements.append(float(m))
            except (TypeError, ValueError):
                pass

        serialised_rows.append(
            {
                "id": r.id,
                "audit_date": r.audit_date.isoformat() if r.audit_date else None,
                "measurements": measurements,
                "judgement": r.judgement,
                "nominal": (r.raw_ocr_json or {}).get("row_data", {}).get("nominal") if isinstance(r.raw_ocr_json, dict) else None,
                "upper_limit": (r.raw_ocr_json or {}).get("row_data", {}).get("upper_limit") if isinstance(r.raw_ocr_json, dict) else None,
                "lower_limit": (r.raw_ocr_json or {}).get("row_data", {}).get("lower_limit") if isinstance(r.raw_ocr_json, dict) else None,
            }
        )

    total = len(rows)
    ok_pct = round(ok_count / total * 100, 1) if total else 0
    nok_pct = round(nok_count / total * 100, 1) if total else 0
    avg_torque = round(sum(all_measurements) / len(all_measurements), 2) if all_measurements else None

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
        },
    }
