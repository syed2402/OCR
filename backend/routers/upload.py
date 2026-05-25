"""
Upload router — POST /upload and GET /uploads/{upload_id}/status

Flow:
  1. Client POSTs a PDF file.
  2. We save it, create an Upload record (status=PROCESSING), and return the
     upload_id immediately so the client can start polling.
  3. A background task runs the full pipeline:
        PDF → page images → preprocessing → Gemini OCR → DB rows (EXTRACTED)
  4. Client polls /uploads/{upload_id}/status until status=COMPLETED|FAILED.
"""

import logging
import os
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from database import get_db
from models import ExtractedOperation, Upload
from services.cloud_storage import upload_review_image
from services.ocr_service import extract_from_image
from services.pdf_processor import pdf_to_images, resolve_poppler_path
from services.standard_template import apply_standard_template, default_template_model, seed_standard_templates

logger = logging.getLogger(__name__)
router = APIRouter()

# Resolve to absolute paths so cv2.imread works regardless of thread CWD
BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _backend_path(env_name: str, default: str) -> Path:
    path = Path(os.getenv(env_name, default))
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


UPLOAD_DIR = _backend_path("UPLOAD_DIR", "static/uploads")
ROW_IMAGES_DIR = _backend_path("ROW_IMAGES_DIR", "static/row_images")
STALE_STARTUP_MINUTES = 5
STALE_PROCESSING_MINUTES = int(os.getenv("STALE_PROCESSING_MINUTES", "8"))
OCR_PAGE_TIMEOUT_SECONDS = int(os.getenv("OCR_PAGE_TIMEOUT_SECONDS", "210"))
OCR_PAGE_WORKERS = max(1, min(4, int(os.getenv("OCR_PAGE_WORKERS", "2"))))


def _quantity_from_row_data(row_data: dict) -> int | None:
    value = row_data.get("quantity") or row_data.get("qty")
    if value is None:
        return None
    try:
        quantity = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, quantity)


def _machine_values_for_quantity(row_data: dict, quantity: int | None) -> list[float]:
    values = []
    for value in row_data.get("measurements") or []:
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            logger.warning("Dropping non-numeric machine value for op %s: %r", row_data.get("operation_number"), value)
    return values


def _extract_from_image_with_timeout(image_path: str) -> dict:
    results: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            results.put(("ok", extract_from_image(image_path)))
        except Exception as exc:
            results.put(("error", exc))

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        status, payload = results.get(timeout=OCR_PAGE_TIMEOUT_SECONDS)
    except queue.Empty:
        return {
            "audit_date": None,
            "sheet_type": "TORQUE",
            "rows": [],
            "raw_response": "",
            "error": f"OCR timed out after {OCR_PAGE_TIMEOUT_SECONDS} seconds",
        }

    if status == "error":
        raise payload
    return payload


def _ocr_page_for_upload(upload_id: str, page_num: int, page_count: int, image_path: str) -> dict:
    logger.info("=" * 70)
    logger.info("PROCESSING PAGE %d/%d: %s", page_num, page_count, image_path)
    logger.info("=" * 70)
    review_image_path = upload_review_image(image_path, upload_id, page_num) or image_path
    result = _extract_from_image_with_timeout(image_path)
    return {
        "page_num": page_num,
        "image_path": image_path,
        "review_image_path": review_image_path,
        "result": result,
    }


def _page_images_for_upload(upload_id: str) -> dict[int, str]:
    images: dict[int, str] = {}
    for img_path in ROW_IMAGES_DIR.glob(f"{upload_id}_page_*.png"):
        try:
            page_num = int(img_path.name.split("_page_")[1].split(".")[0])
        except (IndexError, ValueError):
            continue
        images[page_num] = str(img_path)
    return images


def _pages_with_rows(db: Session, upload_id: str) -> set[int]:
    rows = db.execute(
        text(
            "SELECT DISTINCT (raw_ocr_json->>'page')::int AS p "
            "FROM extracted_operations "
            "WHERE upload_id = :uid AND raw_ocr_json->>'page' IS NOT NULL"
        ),
        {"uid": upload_id},
    ).fetchall()
    return {int(row.p) for row in rows if row.p is not None}


def _reconcile_stale_processing_upload(
    upload: Upload,
    db: Session,
    background_tasks: BackgroundTasks | None = None,
) -> bool:
    """Recover Render uploads when the in-process background task stops mid-run."""
    resumable_failed = (
        upload.status == "FAILED"
        and upload.error_message
        and "Missing page(s)" in upload.error_message
    )
    if upload.status != "PROCESSING" and not resumable_failed:
        return False
    if not upload.created_at:
        return False

    row_count, latest_row_at = (
        db.query(func.count(ExtractedOperation.id), func.max(ExtractedOperation.created_at))
        .filter(ExtractedOperation.upload_id == upload.id)
        .one()
    )
    row_count = int(row_count or 0)
    now = datetime.now()
    changed = False

    if row_count and row_count != (upload.total_rows or 0):
        upload.total_rows = row_count
        changed = True

    page_images = _page_images_for_upload(str(upload.id))
    if (
        row_count == 0
        and (upload.processed_pages or 0) == 0
        and not page_images
        and now - upload.created_at > timedelta(minutes=STALE_STARTUP_MINUTES)
    ):
        upload.status = "FAILED"
        upload.error_message = (
            "Processing worker stopped before page extraction started. "
            "Please retry the upload."
        )
        upload.completed_at = now
        return True

    last_progress_at = latest_row_at or upload.created_at
    if now - last_progress_at <= timedelta(minutes=STALE_PROCESSING_MINUTES):
        return changed

    missing_pages = sorted(set(page_images) - _pages_with_rows(db, str(upload.id)))
    if missing_pages and background_tasks is not None:
        if upload.completed_at and now - upload.completed_at <= timedelta(minutes=STALE_PROCESSING_MINUTES):
            return changed
        db_url = os.getenv(
            "DATABASE_URL",
            "postgresql://quality_user:quality_pass@localhost:5432/stellantis_quality",
        )
        background_tasks.add_task(_resume_missing_pages_background, str(upload.id), missing_pages, db_url)
        upload.status = "PROCESSING"
        upload.error_message = (
            "Render worker paused; resuming OCR for missing page(s): "
            + ", ".join(map(str, missing_pages))
        )
        upload.completed_at = now
        return True

    if row_count > 0 and not missing_pages:
        upload.status = "COMPLETED"
        upload.total_rows = row_count
        upload.error_message = None
        upload.completed_at = now
        return True

    upload.status = "FAILED"
    if missing_pages:
        upload.error_message = (
            "Processing timed out before all pages finished. Missing page(s): "
            + ", ".join(map(str, missing_pages))
        )
    else:
        upload.error_message = "Processing timed out before any rows were saved. Please retry the upload."
    upload.completed_at = now
    return True


def _template_model_from_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    upper = filename.upper()
    if "EBDT" in upper:
        return "EBDT"
    if "EBNA" in upper:
        return "EBNA"
    return default_template_model()


def _parse_audit_date(raw_date) -> date | None:
    if isinstance(raw_date, datetime):
        return raw_date.date()
    if isinstance(raw_date, date):
        return raw_date
    if not raw_date:
        return None
    try:
        return datetime.strptime(str(raw_date), "%Y-%m-%d").date()
    except ValueError:
        return None


def _saved_operation_to_row_data(row: ExtractedOperation) -> dict:
    raw = row.raw_ocr_json or {}
    row_data = dict(raw.get("row_data") or {})
    row_data.update({
        "operation_number": row.operation_number,
        "engine_number": row.engine_number,
        "process_name": row.process_name,
        "quantity": row.quantity,
        "measurements": row.measurements_json or [],
        "judgement": row.judgement,
        "audit_date": row.audit_date.isoformat() if row.audit_date else row_data.get("audit_date"),
        "page": raw.get("page") or row_data.get("page"),
        "raw_response": raw.get("raw_response", ""),
        "row_image_path": row.row_image_path,
    })
    return row_data


def _add_extracted_operation(
    db: Session,
    upload_id: str,
    row_data: dict,
    page_num: int | None = None,
    review_image_path: str | None = None,
    raw_response: str = "",
) -> ExtractedOperation:
    quantity = _quantity_from_row_data(row_data)
    measurements = _machine_values_for_quantity(row_data, quantity)
    audit_date = _parse_audit_date(row_data.get("audit_date"))
    raw_row_data = {**row_data}
    op = ExtractedOperation(
        upload_id=upload_id,
        audit_date=audit_date,
        operation_number=row_data.get("operation_number"),
        engine_number=row_data.get("engine_number"),
        process_name=row_data.get("process_name"),
        judgement=row_data.get("judgement"),
        quantity=quantity,
        measurements_json=measurements,
        raw_ocr_json={
            "page": page_num or row_data.get("page"),
            "raw_response": (raw_response or row_data.get("raw_response") or "")[:4000],
            "row_data": raw_row_data,
            "template": row_data.get("template"),
            "template_model": row_data.get("template_model"),
            "printed_values_source": row_data.get("printed_values_source"),
            "confidence_scores": row_data.get("confidence_scores") or {},
            "unclear_fields": row_data.get("unclear_fields") or [],
        },
        corrected_json=None,
        review_status="EXTRACTED",
        row_image_path=review_image_path or row_data.get("row_image_path"),
    )
    db.add(op)
    return op


# ---------------------------------------------------------------------------
# Background processing
# ---------------------------------------------------------------------------

def _process_upload(upload_id: str, pdf_path: str, db_url: str) -> None:
    """
    Runs in a background task:
      PDF → images → preprocess → OCR → insert rows → mark upload COMPLETED.
    """
    # Re-load .env inside the background task to guarantee all env vars are available
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH, override=True)

    # Ensure poppler is in PATH for subprocess calls
    _pp = resolve_poppler_path()
    logger.info("Upload background task POPPLER_PATH: %s", _pp)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        upload = db.query(Upload).filter(Upload.id == upload_id).first()
        if not upload:
            logger.error("Upload %s not found in background task", upload_id)
            return

        # The workbook/rules are the source of truth for printed values. Refresh
        # before each upload so replaced templates never leave stale DB rows.
        seed_standard_templates(db, force=True)

        # Step 1 — PDF → page images (pass poppler_path explicitly)
        def mark_page_rendered(page_num: int, _image_path: str) -> None:
            upload.processed_pages = max(upload.processed_pages or 0, page_num)
            upload.error_message = f"Rendered page {page_num}; OCR will start after page extraction."
            db.commit()

        image_paths = pdf_to_images(
            pdf_path=pdf_path,
            output_dir=str(ROW_IMAGES_DIR),
            upload_id=upload_id,
            poppler_path=_pp or None,
            on_page_saved=mark_page_rendered,
        )

        total_rows = 0
        last_known_date: str | None = None
        failed_pages: list[str] = []

        preferred_template_model = _template_model_from_filename(upload.original_filename)

        page_count = len(image_paths)
        workers = min(OCR_PAGE_WORKERS, page_count)
        upload.error_message = f"Running OCR on {page_count} page(s) with {workers} worker(s)."
        db.commit()

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_ocr_page_for_upload, str(upload_id), page_num, page_count, image_path): page_num
                for page_num, image_path in enumerate(image_paths, start=1)
            }

            for future in as_completed(futures):
                page_num = futures[future]
                try:
                    page_result = future.result()
                    result = page_result["result"]
                    review_image_path = page_result["review_image_path"]
                    logger.info("OCR result keys: %s", list(result.keys()))
                    logger.info("Error in result: %s", result.get("error"))
                    logger.info("Rows in result: %d", len(result.get("rows", [])))
                except Exception as ocr_error:
                    logger.error("OCR EXCEPTION on page %d: %s", page_num, ocr_error, exc_info=True)
                    failed_pages.append(f"page {page_num}: {ocr_error}")
                    upload.processed_pages = max(upload.processed_pages or 0, page_num)
                    db.commit()
                    continue

                if result.get("error"):
                    logger.warning("OCR error on page %d: %s", page_num, result["error"])
                    failed_pages.append(f"page {page_num}: {result['error']}")
                    upload.processed_pages = max(upload.processed_pages or 0, page_num)
                    db.commit()
                    continue

                page_rows = result.get("rows", [])
                page_rows, template_model = apply_standard_template(page_rows, db, preferred_template_model)
                if template_model:
                    upload.error_message = f"Running OCR using {template_model} template; completed page {page_num}/{page_count}."
                    db.commit()
                if not page_rows:
                    message = "OCR returned 0 rows"
                    logger.warning("%s on page %d", message, page_num)
                    failed_pages.append(f"page {page_num}: {message}")
                    upload.processed_pages = max(upload.processed_pages or 0, page_num)
                    upload.total_rows = total_rows
                    db.commit()
                    continue

                logger.info(
                    "Page %d -> %d rows extracted (sheet_type=%s)",
                    page_num, len(page_rows), result.get("sheet_type", "?"),
                )

                page_date = result.get("audit_date")
                if page_date:
                    last_known_date = page_date

                for row_data in page_rows:
                    if not row_data.get("audit_date") and last_known_date:
                        row_data["audit_date"] = last_known_date
                    _add_extracted_operation(
                        db,
                        str(upload_id),
                        row_data,
                        page_num=page_num,
                        review_image_path=review_image_path,
                        raw_response=result.get("raw_response", ""),
                    )
                    total_rows += 1

                upload.processed_pages = max(upload.processed_pages or 0, page_num)
                upload.total_rows = total_rows
                db.commit()

        if failed_pages:
            upload.status = "FAILED"
            upload.error_message = (
                "Some pages were not extracted: " + "; ".join(failed_pages[:8])
            )
        else:
            upload.status = "COMPLETED"
        upload.total_rows = total_rows
        upload.completed_at = datetime.utcnow()
        db.commit()
        logger.info(
            "Upload %s finished with status %s: %d rows from %d pages",
            upload_id, upload.status, total_rows, len(image_paths),
        )

    except Exception as exc:
        logger.exception("Fatal error processing upload %s", upload_id)
        try:
            upload = db.query(Upload).filter(Upload.id == upload_id).first()
            if upload:
                upload.status = "FAILED"
                upload.error_message = str(exc)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Accept a PDF, save it, start background OCR pipeline, return upload_id."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ROW_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    upload_id = str(uuid.uuid4())
    safe_name = f"{upload_id}.pdf"
    pdf_path = UPLOAD_DIR / safe_name

    content = await file.read()
    with open(pdf_path, "wb") as f:
        f.write(content)

    upload = Upload(
        id=upload_id,
        original_filename=file.filename,
        pdf_path=str(pdf_path),
        status="PROCESSING",
    )
    db.add(upload)
    db.commit()

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://quality_user:quality_pass@localhost:5432/stellantis_quality",
    )
    background_tasks.add_task(_process_upload, upload_id, str(pdf_path), db_url)

    return {
        "status": "success",
        "upload_id": upload_id,
        "filename": file.filename,
    }


@router.get("/uploads/{upload_id}/status")
def get_upload_status(
    upload_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Poll this endpoint to track processing progress."""
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    if _reconcile_stale_processing_upload(upload, db, background_tasks):
        db.commit()
        db.refresh(upload)

    return {
        "upload_id": str(upload.id),
        "status": upload.status,
        "total_rows": upload.total_rows,
        "processed_pages": upload.processed_pages,
        "original_filename": upload.original_filename,
        "error_message": upload.error_message,
        "created_at": upload.created_at.isoformat() if upload.created_at else None,
        "completed_at": upload.completed_at.isoformat() if upload.completed_at else None,
    }


@router.post("/uploads/{upload_id}/reapply-template")
def reapply_template(upload_id: str, db: Session = Depends(get_db)):
    """Repair saved rows from the standard template without running OCR again."""
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    existing_rows = (
        db.query(ExtractedOperation)
        .filter(ExtractedOperation.upload_id == upload_id)
        .order_by(ExtractedOperation.id)
        .all()
    )
    if not existing_rows:
        raise HTTPException(status_code=400, detail="No extracted rows found for this upload")

    seed_standard_templates(db, force=True)
    row_data = [_saved_operation_to_row_data(row) for row in existing_rows]
    preferred_template_model = _template_model_from_filename(upload.original_filename)
    repaired_rows, template_model = apply_standard_template(row_data, db, preferred_template_model)
    if not repaired_rows:
        raise HTTPException(status_code=400, detail="Template reapply produced no rows")

    db.query(ExtractedOperation).filter(ExtractedOperation.upload_id == upload_id).delete(synchronize_session=False)
    for row in repaired_rows:
        _add_extracted_operation(db, upload_id, row)

    upload.total_rows = len(repaired_rows)
    upload.status = "COMPLETED"
    upload.error_message = f"Reapplied {template_model or 'standard'} template without OCR."
    upload.completed_at = datetime.utcnow()
    db.commit()

    return {
        "status": "success",
        "upload_id": upload_id,
        "template_model": template_model,
        "total_rows": upload.total_rows,
    }


@router.get("/uploads/{upload_id}/pages")
def get_upload_pages(upload_id: str, db: Session = Depends(get_db)):
    """
    Return per-page summary for an upload: page number, row count, image path.
    Used to identify pages that failed OCR so they can be retried.
    """
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    # Discover page images that exist on disk for this upload
    ROW_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    import glob as _glob
    pattern = str(ROW_IMAGES_DIR / f"{upload_id}_page_*.png")
    image_files = sorted(_glob.glob(pattern))

    # Count rows already extracted per page
    from sqlalchemy import text as _text
    rows_per_page: dict[int, int] = {}
    result = db.execute(
        _text(
            "SELECT (raw_ocr_json->>'page')::int AS p, COUNT(*) AS cnt "
            "FROM extracted_operations "
            "WHERE upload_id = :uid AND raw_ocr_json->>'page' IS NOT NULL "
            "GROUP BY p"
        ),
        {"uid": upload_id},
    ).fetchall()
    for row in result:
        rows_per_page[row.p] = row.cnt

    pages = []
    for img_path in image_files:
        fname = os.path.basename(img_path)
        # filename format: {upload_id}_page_001.png
        try:
            page_num = int(fname.split("_page_")[1].split(".")[0])
        except (IndexError, ValueError):
            continue
        pages.append({
            "page": page_num,
            "row_count": rows_per_page.get(page_num, 0),
            "image_path": img_path,
        })

    return pages


@router.get("/uploads/{upload_id}/file")
def get_upload_file(upload_id: str, db: Session = Depends(get_db)):
    """Open/download the original uploaded file."""
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")
    if not upload.pdf_path or not os.path.exists(upload.pdf_path):
        raise HTTPException(status_code=404, detail="Uploaded file not found")

    return FileResponse(
        upload.pdf_path,
        filename=upload.original_filename,
        media_type="application/pdf",
    )


def _resume_missing_pages_background(upload_id: str, missing_pages: list[int], db_url: str) -> None:
    logger.info("Resuming upload %s for missing pages: %s", upload_id, missing_pages)
    page_images = _page_images_for_upload(upload_id)
    for page_num in missing_pages:
        image_path = page_images.get(page_num)
        if not image_path:
            logger.warning("Cannot resume upload %s page %d: page image missing", upload_id, page_num)
            continue
        _retry_page_background(upload_id, page_num, image_path, db_url)


def _retry_page_background(upload_id: str, page_num: int, image_path: str, db_url: str) -> None:
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH, override=True)
    _pp = resolve_poppler_path()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        logger.info("Retrying OCR for upload %s page %d", upload_id, page_num)
        review_image_path = upload_review_image(image_path, upload_id, page_num) or image_path
        result = _extract_from_image_with_timeout(image_path)

        if result.get("error"):
            logger.warning("Retry OCR error on page %d: %s", page_num, result["error"])
            return

        # Use date from this page, or fall back to the most recent date in the DB for this upload
        page_date = result.get("audit_date")
        if not page_date:
            from sqlalchemy import text as _t
            row = db.execute(
                _t("SELECT audit_date FROM extracted_operations WHERE upload_id=:uid AND audit_date IS NOT NULL ORDER BY id DESC LIMIT 1"),
                {"uid": upload_id},
            ).fetchone()
            if row and row.audit_date:
                page_date = row.audit_date.isoformat()

        upload = db.query(Upload).filter(Upload.id == upload_id).first()
        preferred_template_model = _template_model_from_filename(upload.original_filename if upload else None)
        seed_standard_templates(db, force=True)
        retry_rows = result.get("rows", [])
        retry_rows, template_model = apply_standard_template(retry_rows, db, preferred_template_model)
        if not retry_rows:
            logger.warning("Retry page %d returned 0 rows for upload %s", page_num, upload_id)
            return

        from sqlalchemy import text as _delete_text
        deleted = db.execute(
            _delete_text(
                "DELETE FROM extracted_operations "
                "WHERE upload_id=:uid AND (raw_ocr_json->>'page')::int=:page"
            ),
            {"uid": upload_id, "page": page_num},
        )
        logger.info(
            "Retry page %d: deleted %d old rows for upload %s",
            page_num, deleted.rowcount or 0, upload_id,
        )

        rows_added = 0
        for row_data in retry_rows:
            if not row_data.get("audit_date") and page_date:
                row_data["audit_date"] = page_date
            row_data["template_model"] = row_data.get("template_model") or template_model
            _add_extracted_operation(
                db,
                upload_id,
                row_data,
                page_num=page_num,
                review_image_path=review_image_path,
                raw_response=result.get("raw_response", ""),
            )
            rows_added += 1

        # Update upload totals
        upload = db.query(Upload).filter(Upload.id == upload_id).first()
        if upload:
            import glob as _glob
            from sqlalchemy import text as _text

            upload.total_rows = db.execute(
                _text("SELECT COUNT(*) FROM extracted_operations WHERE upload_id=:uid"),
                {"uid": upload_id},
            ).scalar() or 0

            page_files = _glob.glob(str(ROW_IMAGES_DIR / f"{upload_id}_page_*.png"))
            pages_with_rows = {
                r.p for r in db.execute(
                    _text(
                        "SELECT DISTINCT (raw_ocr_json->>'page')::int AS p "
                        "FROM extracted_operations "
                        "WHERE upload_id=:uid AND raw_ocr_json->>'page' IS NOT NULL"
                    ),
                    {"uid": upload_id},
                ).fetchall()
            }
            expected_pages = set()
            for path in page_files:
                try:
                    expected_pages.add(int(os.path.basename(path).split("_page_")[1].split(".")[0]))
                except (IndexError, ValueError):
                    continue
            missing_pages = sorted(expected_pages - pages_with_rows)
            if missing_pages:
                upload.status = "FAILED"
                upload.error_message = "Pages still missing rows: " + ", ".join(map(str, missing_pages))
            else:
                upload.status = "COMPLETED"
                upload.error_message = None
        db.commit()
        logger.info("Retry page %d: added %d rows for upload %s", page_num, rows_added, upload_id)
    except Exception:
        logger.exception("Retry failed for upload %s page %d", upload_id, page_num)
    finally:
        db.close()


@router.post("/uploads/{upload_id}/retry-page/{page_num}")
def retry_page(
    upload_id: str,
    page_num: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Re-run OCR on a single page that previously failed (e.g. due to rate limiting)."""
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    # Find the image for this page
    img_path = ROW_IMAGES_DIR / f"{upload_id}_page_{page_num:03d}.png"
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"Page image not found: {img_path.name}")

    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://quality_user:quality_pass@localhost:5432/stellantis_quality",
    )
    background_tasks.add_task(_retry_page_background, upload_id, page_num, str(img_path), db_url)

    return {"status": "retrying", "upload_id": upload_id, "page": page_num}


@router.delete("/uploads/{upload_id}")
def delete_upload(upload_id: str, db: Session = Depends(get_db)):
    """Delete an upload and all its extracted rows + associated files."""
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    # Delete all extracted rows (and their cropped images)
    rows = db.query(ExtractedOperation).filter(ExtractedOperation.upload_id == upload_id).all()
    deleted_files = set()
    for row in rows:
        if row.row_image_path and row.row_image_path.startswith(("http://", "https://")):
            continue
        if row.row_image_path and row.row_image_path not in deleted_files:
            try:
                os.remove(row.row_image_path)
                deleted_files.add(row.row_image_path)
            except FileNotFoundError:
                pass
        db.delete(row)

    # Delete page images (pattern: {upload_id}_page_*.png)
    import glob as _glob
    for img in _glob.glob(str(ROW_IMAGES_DIR / f"{upload_id}_page_*.png")):
        try:
            os.remove(img)
        except FileNotFoundError:
            pass

    # Delete the PDF file
    if upload.pdf_path:
        try:
            os.remove(upload.pdf_path)
        except FileNotFoundError:
            pass

    db.delete(upload)
    db.commit()
    return {"deleted": upload_id}


@router.get("/uploads")
def list_uploads(background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Return all uploads ordered by most recent first."""
    uploads = db.query(Upload).order_by(Upload.created_at.desc()).limit(50).all()
    changed = False
    for upload in uploads:
        changed = _reconcile_stale_processing_upload(upload, db, background_tasks) or changed
    if changed:
        db.commit()
        for upload in uploads:
            db.refresh(upload)
    return [
        {
            "upload_id": str(u.id),
            "status": u.status,
            "total_rows": u.total_rows,
            "original_filename": u.original_filename,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in uploads
    ]
