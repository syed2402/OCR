"""
Stellantis Manufacturing Quality Analytics Platform — FastAPI entry point.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

BACKEND_DIR = Path(__file__).resolve().parent
ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH, override=True)

from database import init_db
from routers import analytics, review, upload
from services.pdf_processor import resolve_poppler_path
from services.standard_template import seed_standard_templates

# Add poppler to process PATH so pdf2image can find pdfinfo + pdftoppm
# regardless of which thread calls it.
resolve_poppler_path()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Stellantis Quality Analytics API",
    description="Manufacturing audit sheet digitisation and analytics platform.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# CORS — allow the Vite dev server and any localhost origin during pilot
# ---------------------------------------------------------------------------
frontend_url = os.getenv("FRONTEND_URL")
allow_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://localhost:8001",
]
if frontend_url:
    allow_origins.append(frontend_url.rstrip("/"))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Static file serving — row images rendered during OCR
# ---------------------------------------------------------------------------
def _backend_path(env_name: str, default: str) -> Path:
    path = Path(os.getenv(env_name, default))
    if not path.is_absolute():
        path = BACKEND_DIR / path
    return path.resolve()


ROW_IMAGES_DIR = _backend_path("ROW_IMAGES_DIR", "static/row_images")
UPLOAD_DIR = _backend_path("UPLOAD_DIR", "static/uploads")
STATIC_ROOT = ROW_IMAGES_DIR.parent
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
ROW_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(upload.router, tags=["Upload"])
app.include_router(review.router, tags=["Review"])
app.include_router(analytics.router, tags=["Analytics"])


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
@app.on_event("startup")
def on_startup():
    logger.info("Initialising database schema…")
    init_db()
    try:
        from database import SessionLocal
        db = SessionLocal()
        try:
            seed_standard_templates(db)
        finally:
            db.close()
    except Exception:
        logger.exception("Standard template seed failed")
    logger.info("Ready.")


@app.get("/health")
def health():
    return {"status": "ok"}
