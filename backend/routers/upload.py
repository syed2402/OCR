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
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from database import get_db
from models import ExtractedOperation, Upload
from services.cloud_storage import upload_review_image
from services.ocr_service import extract_from_image
from services.pdf_processor import pdf_to_images, resolve_poppler_path

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

        # Process pages sequentially to avoid rate limits
        for page_num, image_path in enumerate(image_paths, start=1):
            upload.error_message = f"Running OCR on page {page_num}/{len(image_paths)}."
            db.commit()
            logger.info("=" * 70)
            logger.info("PROCESSING PAGE %d/%d: %s", page_num, len(image_paths), image_path)
            logger.info("=" * 70)
            review_image_path = upload_review_image(image_path, upload_id, page_num) or image_path
            
            try:
                result = extract_from_image(image_path)
                logger.info("OCR result keys: %s", list(result.keys()))
                logger.info("Error in result: %s", result.get("error"))
                logger.info("Rows in result: %d", len(result.get("rows", [])))
            except Exception as ocr_error:
                logger.error("OCR EXCEPTION on page %d: %s", page_num, ocr_error, exc_info=True)
                failed_pages.append(f"page {page_num}: {ocr_error}")
                # Mark page as processed but continue
                upload.processed_pages = page_num
                db.commit()
                # Wait before retrying next page
                time.sleep(10)
                continue

            if result.get("error"):
                logger.warning("OCR error on page %d: %s", page_num, result["error"])
                failed_pages.append(f"page {page_num}: {result['error']}")
                upload.processed_pages = page_num
                db.commit()
                time.sleep(10)
                continue

            page_rows = result.get("rows", [])
            if not page_rows:
                message = "OCR returned 0 rows"
                logger.warning("%s on page %d", message, page_num)
                failed_pages.append(f"page {page_num}: {message}")
                upload.processed_pages = page_num
                upload.total_rows = total_rows
                db.commit()
                time.sleep(10)
                continue

            logger.info(
                "Page %d → %d rows extracted (sheet_type=%s)",
                page_num, len(page_rows), result.get("sheet_type", "?"),
            )

            page_date = result.get("audit_date")
            if page_date:
                last_known_date = page_date

            for row_data in page_rows:
                quantity = _quantity_from_row_data(row_data)
                measurements = _machine_values_for_quantity(row_data, quantity)
                audit_date = None
                raw_date = row_data.get("audit_date") or last_known_date
                if raw_date:
                    try:
                        audit_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
                    except ValueError:
                        pass

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
                        "page": page_num,
                        "raw_response": result.get("raw_response", "")[:4000],
                        "row_data": row_data,
                        "confidence_scores": row_data.get("confidence_scores") or {},
                        "unclear_fields": row_data.get("unclear_fields") or [],
                    },
                    corrected_json=None,
                    review_status="EXTRACTED",
                    row_image_path=review_image_path,
                )
                db.add(op)
                total_rows += 1

            upload.processed_pages = page_num
            upload.total_rows = total_rows
            db.commit()
            
            # Wait between pages to avoid rate limits
            if page_num < len(image_paths):
                logger.info("Waiting 6 seconds before next page...")
                time.sleep(6)

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
def get_upload_status(upload_id: str, db: Session = Depends(get_db)):
    """Poll this endpoint to track processing progress."""
    upload = db.query(Upload).filter(Upload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found")

    if (
        upload.status == "PROCESSING"
        and upload.created_at
        and upload.processed_pages == 0
        and upload.total_rows == 0
        and datetime.now() - upload.created_at > timedelta(minutes=STALE_STARTUP_MINUTES)
    ):
        upload.status = "FAILED"
        upload.error_message = (
            "Processing worker stopped before page extraction started. "
            "Please retry the upload."
        )
        upload.completed_at = datetime.now()
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
        result = extract_from_image(image_path)

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

        retry_rows = result.get("rows", [])
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
            quantity = _quantity_from_row_data(row_data)
            measurements = _machine_values_for_quantity(row_data, quantity)
            audit_date = None
            raw_date = row_data.get("audit_date") or page_date
            if raw_date:
                try:
                    audit_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
                except ValueError:
                    pass

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
                    "page": page_num,
                    "raw_response": result.get("raw_response", "")[:4000],
                    "row_data": row_data,
                    "confidence_scores": row_data.get("confidence_scores") or {},
                    "unclear_fields": row_data.get("unclear_fields") or [],
                },
                corrected_json=None,
                review_status="EXTRACTED",
                row_image_path=review_image_path,
            )
            db.add(op)
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
def list_uploads(db: Session = Depends(get_db)):
    """Return all uploads ordered by most recent first."""
    uploads = db.query(Upload).order_by(Upload.created_at.desc()).limit(50).all()
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
