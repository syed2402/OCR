"""Standard EBNA/EBDT torque template support.

The Excel workbook is the source of truth for printed fields. OCR should still
read handwritten values from uploaded PDFs, but operation/process/quantity/spec
values are corrected from these template rows before saving.
"""

from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from openpyxl import load_workbook
from sqlalchemy.orm import Session

from models import StandardTemplateRow

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE_PATH = BACKEND_DIR / "data" / "standard_torque_template.xlsx"

TORQUE_SHEETS = {
    "Torque Audit Sheet - EBNA": {
        "model": "EBNA",
        "start": 5,
        "op": 1,
        "process": 2,
        "tightening_equipment": 6,
        "quantity": 8,
        "tightening_torque": 9,
        "checking_equipment": 11,
    },
    "Torque Audit Sheet - EBDT": {
        "model": "EBDT",
        "start": 5,
        "op": 1,
        "process": 3,
        "tightening_equipment": 7,
        "tightening_part": 9,
        "quantity": 10,
        "tightening_torque": 11,
        "engineering_spec": 13,
        "checking_equipment": 14,
    },
}


def _excluded_ops() -> set[str]:
    raw = os.getenv("EXCLUDED_TEMPLATE_OPS", "1360")
    return {re.sub(r"\D", "", item) for item in re.split(r"[,;\s]+", raw) if re.sub(r"\D", "", item)}


def _clean_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\r", "\n").replace("ą", "+/-").replace("±", "+/-")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _clean_op(value) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return digits or None


def _clean_quantity(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def _template_path() -> Path:
    configured = os.getenv("STANDARD_TEMPLATE_XLSX")
    return Path(configured).resolve() if configured else DEFAULT_TEMPLATE_PATH


def _extract_rows_from_workbook(path: Path) -> list[dict]:
    if not path.exists():
        logger.warning("Standard template workbook not found: %s", path)
        return []

    workbook = load_workbook(path, data_only=True, read_only=True)
    extracted: list[dict] = []

    for sheet_name, spec in TORQUE_SHEETS.items():
        if sheet_name not in workbook.sheetnames:
            logger.warning("Standard template sheet missing: %s", sheet_name)
            continue

        sheet = workbook[sheet_name]
        current_op: str | None = None
        current_process: str | None = None
        current_equipment: str | None = None
        sequence_by_op: dict[str, int] = {}

        for row_num in range(spec["start"], sheet.max_row + 1):
            op = _clean_op(sheet.cell(row_num, spec["op"]).value)
            if op:
                current_op = op
                current_process = _clean_text(sheet.cell(row_num, spec["process"]).value) or current_process
                current_equipment = (
                    _clean_text(sheet.cell(row_num, spec["tightening_equipment"]).value)
                    or current_equipment
                )

            quantity = _clean_quantity(sheet.cell(row_num, spec["quantity"]).value)
            if not current_op or current_op in _excluded_ops() or quantity is None:
                continue

            process_name = _clean_text(sheet.cell(row_num, spec["process"]).value) or current_process
            tightening_equipment = (
                _clean_text(sheet.cell(row_num, spec["tightening_equipment"]).value)
                or current_equipment
            )
            row = {
                "model": spec["model"],
                "sheet_name": sheet_name,
                "operation_number": current_op,
                "sequence": sequence_by_op.get(current_op, 0) + 1,
                "process_name": process_name,
                "tightening_equipment": tightening_equipment,
                "tightening_part": _clean_text(sheet.cell(row_num, spec.get("tightening_part", 0)).value)
                if spec.get("tightening_part")
                else None,
                "quantity": quantity,
                "tightening_torque": _clean_text(sheet.cell(row_num, spec["tightening_torque"]).value),
                "engineering_spec": _clean_text(sheet.cell(row_num, spec.get("engineering_spec", 0)).value)
                if spec.get("engineering_spec")
                else None,
                "checking_equipment": _clean_text(sheet.cell(row_num, spec["checking_equipment"]).value),
                "source_row": row_num,
            }
            if any(row.get(k) for k in ("process_name", "tightening_torque", "engineering_spec")):
                sequence_by_op[current_op] = row["sequence"]
                extracted.append(row)

    return extracted


def seed_standard_templates(db: Session, force: bool = False) -> int:
    """Seed template rows from the standard workbook if the DB table is empty."""
    excluded_ops = _excluded_ops()
    if excluded_ops:
        deleted = (
            db.query(StandardTemplateRow)
            .filter(StandardTemplateRow.operation_number.in_(excluded_ops))
            .delete(synchronize_session=False)
        )
        if deleted:
            logger.info("Removed %d excluded standard template rows: %s", deleted, ", ".join(sorted(excluded_ops)))
            db.commit()

    existing = db.query(StandardTemplateRow).count()
    if existing and not force:
        return existing
    if force:
        db.query(StandardTemplateRow).delete()

    rows = _extract_rows_from_workbook(_template_path())
    for row in rows:
        db.add(StandardTemplateRow(**row))
    db.commit()
    logger.info("Seeded %d standard template rows", len(rows))
    _cached_workbook_rows.cache_clear()
    return len(rows)


def _row_to_dict(row: StandardTemplateRow) -> dict:
    return {
        "model": row.model,
        "sheet_name": row.sheet_name,
        "operation_number": row.operation_number,
        "sequence": row.sequence,
        "process_name": row.process_name,
        "tightening_equipment": row.tightening_equipment,
        "tightening_part": row.tightening_part,
        "quantity": row.quantity,
        "tightening_torque": row.tightening_torque,
        "engineering_spec": row.engineering_spec,
        "checking_equipment": row.checking_equipment,
        "source_row": row.source_row,
    }


def _load_db_template_rows(db: Session) -> list[dict]:
    rows = (
        db.query(StandardTemplateRow)
        .order_by(StandardTemplateRow.model, StandardTemplateRow.id)
        .all()
    )
    if rows:
        return [_row_to_dict(row) for row in rows]

    seed_standard_templates(db)
    rows = (
        db.query(StandardTemplateRow)
        .order_by(StandardTemplateRow.model, StandardTemplateRow.id)
        .all()
    )
    return [_row_to_dict(row) for row in rows]


@lru_cache(maxsize=1)
def _cached_workbook_rows() -> tuple[dict, ...]:
    return tuple(_extract_rows_from_workbook(_template_path()))


def template_prompt_context(max_rows: int = 140) -> str:
    """Compact template context for the OCR prompt."""
    rows = list(_cached_workbook_rows())[:max_rows]
    if not rows:
        return ""
    lines = [
        "STANDARD PRINTED TORQUE TEMPLATE (use for op/process/quantity/spec; read handwritten Actual values from image):"
    ]
    for row in rows:
        part = f";part={row['tightening_part']}" if row.get("tightening_part") else ""
        lines.append(
            f"{row['model']}|op={row['operation_number']}|seq={row['sequence']}|qty={row['quantity']}"
            f"|process={row.get('process_name') or ''}{part}|spec={row.get('engineering_spec') or row.get('tightening_torque') or ''}"
        )
    return "\n".join(lines)


def _choose_model(ocr_rows: Iterable[dict], template_rows: list[dict], preferred_model: str | None = None) -> str | None:
    if preferred_model:
        preferred = preferred_model.upper()
        if any(row["model"] == preferred for row in template_rows):
            return preferred

    ocr_ops = [_clean_op(row.get("operation_number")) for row in ocr_rows]
    ocr_ops = [op for op in ocr_ops if op]
    if not ocr_ops:
        return None

    scores: dict[str, int] = {}
    for model in {row["model"] for row in template_rows}:
        model_ops = {row["operation_number"] for row in template_rows if row["model"] == model}
        scores[model] = sum(1 for op in ocr_ops if op in model_ops)
    return max(scores, key=scores.get) if scores else None


def _parse_limits(text: str | None) -> tuple[float | None, float | None, float | None]:
    if not text:
        return None, None, None
    normalized = text.replace(",", ".")
    range_matches = re.findall(r"(-?\d+(?:\.\d+)?)\s*(?:~|-|to)\s*(-?\d+(?:\.\d+)?)", normalized, flags=re.I)
    lower = upper = None
    if range_matches:
        a, b = range_matches[-1]
        first, second = float(a), float(b)
        lower, upper = min(first, second), max(first, second)

    torque_match = re.search(r"(?:torque|final tight|final torque)\s*:?\s*(-?\d+(?:\.\d+)?)", normalized, flags=re.I)
    nominal = float(torque_match.group(1)) if torque_match else None
    if nominal is None:
        first_number = re.search(r"-?\d+(?:\.\d+)?", normalized)
        nominal = float(first_number.group(0)) if first_number else None
    return nominal, lower, upper


def apply_standard_template(
    ocr_rows: list[dict],
    db: Session,
    preferred_model: str | None = None,
) -> tuple[list[dict], str | None]:
    """Return OCR rows with printed fields overwritten from the template DB."""
    if not ocr_rows:
        return ocr_rows, None

    template_rows = _load_db_template_rows(db)
    model = _choose_model(ocr_rows, template_rows, preferred_model)
    if not model:
        return ocr_rows, None

    by_op: dict[str, list[dict]] = {}
    for template in template_rows:
        if template["model"] != model:
            continue
        by_op.setdefault(template["operation_number"], []).append(template)

    used_by_op: dict[str, int] = {}
    corrected: list[dict] = []
    for row in ocr_rows:
        op = _clean_op(row.get("operation_number"))
        candidates = by_op.get(op or "")
        if not op or not candidates:
            corrected.append(row)
            continue

        index = used_by_op.get(op, 0)
        template = candidates[min(index, len(candidates) - 1)]
        used_by_op[op] = index + 1

        spec_text = template.get("engineering_spec") or template.get("tightening_torque")
        nominal, lower, upper = _parse_limits(spec_text)
        enriched = {
            **row,
            "operation_number": template["operation_number"],
            "process_name": template.get("process_name") or row.get("process_name"),
            "process_description": template.get("process_name") or row.get("process_description"),
            "quantity": template.get("quantity"),
            "nominal": nominal if nominal is not None else row.get("nominal"),
            "upper_limit": upper if upper is not None else row.get("upper_limit"),
            "lower_limit": lower if lower is not None else row.get("lower_limit"),
            "template": template,
            "template_model": model,
        }
        corrected.append(enriched)

    return corrected, model
