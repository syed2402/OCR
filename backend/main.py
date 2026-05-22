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

from database import init_db
from routers import analytics, review, upload
from services.pdf_processor import resolve_poppler_path

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH, override=True)

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
STATIC_ROOT = Path(os.getenv("ROW_IMAGES_DIR", "static/row_images")).resolve().parent
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
Path(os.getenv("ROW_IMAGES_DIR", "static/row_images")).resolve().mkdir(parents=True, exist_ok=True)
Path(os.getenv("UPLOAD_DIR", "static/uploads")).resolve().mkdir(parents=True, exist_ok=True)

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
    logger.info("Ready.")


@app.get("/health")
def health():
    return {"status": "ok"}
