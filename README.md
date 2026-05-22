# Stellantis Manufacturing Quality Analytics Platform

A pilot platform that digitises Stellantis audit sheets via Gemini Vision OCR,
routes every extracted row through a mandatory human review layer, and exposes
operation-wise historical quality analytics as the core deliverable.

---

## Architecture

```
PDF Upload → Gemini Vision OCR → Human Review & Approval → Analytics Dashboard
```

- **Backend** — Python FastAPI (port 8000)
- **Frontend** — React + Vite + Tailwind CSS (port 5173)
- **Database** — PostgreSQL 15
- **OCR** — Google Gemini 1.5 Flash (Vision)
- **Image processing** — OpenCV + pdf2image

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | |
| Node.js | 20+ | |
| PostgreSQL | 15 | or use Docker |
| Poppler | latest | PDF rendering — see below |
| Docker (optional) | latest | for one-command setup |

### Poppler (Windows)

pdf2image requires Poppler binaries.

1. Download from https://github.com/oschwartz10612/poppler-windows/releases
2. Extract to `C:\poppler`
3. Add `C:\poppler\Library\bin` to your PATH, OR
4. Set `POPPLER_PATH=C:\poppler\Library\bin` in your `.env`

---

## Quick Start (Local)

### 1. Clone and configure

```bash
# Copy env template
cp .env.example .env
# Edit .env and set your GEMINI_API_KEY
```

### 2. Start PostgreSQL

```bash
# Option A — Docker (easiest)
docker-compose up -d postgres

# Option B — local PostgreSQL
# Create database: stellantis_quality
# User: quality_user / Pass: quality_pass
psql -U postgres -c "CREATE USER quality_user WITH PASSWORD 'quality_pass';"
psql -U postgres -c "CREATE DATABASE stellantis_quality OWNER quality_user;"
```

### 3. Backend

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

The API creates all tables automatically on startup.

### 4. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

---

## Quick Start (Docker — full stack)

```bash
cp .env.example .env
# Set GEMINI_API_KEY in .env

docker-compose up --build
```

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API docs: http://localhost:8000/docs

---

## Demo (No PDF needed)

Seed the database with pre-approved sample data for the analytics demo:

```bash
cd backend
python seed_demo.py
```

Then open http://localhost:5173/analytics and select any operation.

---

## Workflow

### Step 1 — Upload
Navigate to **Upload** and drop a Stellantis audit PDF.
The system converts pages to images, preprocesses with OpenCV, and extracts
all rows via Gemini Vision. Processing runs in the background; a progress
indicator shows live updates.

### Step 2 — Review
Navigate to **Review** and select the upload.
For each extracted row you see the original page image alongside the
extracted fields. Correct any OCR mistakes inline, then:
- **Approve** (A) — row enters the analytics database
- **Reject** (R) — row is discarded

> **Critical rule**: only APPROVED rows appear in analytics. This is enforced
> server-side in every analytics query.

### Step 3 — Analytics
Navigate to **Analytics**, select an operation from the sidebar, choose a
date range, and explore:
- Historical measurements table (dynamic columns per operation)
- Trend chart (one line per measurement slot)
- OK/NOK summary statistics

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/upload` | Upload PDF; returns `upload_id` |
| `GET` | `/uploads/{id}/status` | Poll extraction progress |
| `GET` | `/uploads` | List all uploads |
| `GET` | `/uploads/{id}/rows` | All extracted rows for review |
| `PUT` | `/review-row/{id}` | Save corrections → REVIEWED |
| `POST` | `/approve-row/{id}` | Approve → APPROVED |
| `POST` | `/reject-row/{id}` | Reject → REJECTED |
| `GET` | `/operations` | Distinct approved operations |
| `GET` | `/analytics` | Historical data for one operation |

Full interactive docs: http://localhost:8000/docs

---

## Database

Single primary table:

```sql
extracted_operations (
    id, upload_id, audit_date, operation_number, process_name,
    judgement, measurements_json,   -- e.g. [33, 33, 33, 33]
    raw_ocr_json, corrected_json,
    review_status,                  -- EXTRACTED | REVIEWED | APPROVED | REJECTED
    row_image_path, reviewed_at, created_at
)
```

The analytics engine unconditionally filters `WHERE review_status = 'APPROVED'`.

---

## Project Structure

```
OCR_ANALYTICS_PLATFORM_PILOT1/
├── backend/
│   ├── main.py                  FastAPI entry point
│   ├── models.py                SQLAlchemy ORM models
│   ├── database.py              DB connection & session
│   ├── schema.sql               DDL (auto-applied by SQLAlchemy on startup)
│   ├── seed_demo.py             Demo data seed script
│   ├── routers/
│   │   ├── upload.py            POST /upload, status polling
│   │   ├── review.py            Review/approve/reject endpoints
│   │   └── analytics.py        Operations list + analytics query
│   └── services/
│       ├── pdf_processor.py     pdf2image page extraction
│       ├── image_preprocessor.py  OpenCV pipeline
│       └── ocr_service.py       Gemini Vision extraction
├── frontend/
│   └── src/
│       ├── pages/
│       │   ├── UploadPage.tsx   Screen 1 — drag-drop upload
│       │   ├── ReviewPage.tsx   Screen 2 — human review (critical)
│       │   └── AnalyticsPage.tsx Screen 3 — analytics explorer
│       └── components/
│           ├── MeasurementsTable.tsx  AG Grid dynamic columns
│           ├── TrendChart.tsx         Recharts line chart
│           └── OkNokSummary.tsx       Stats cards
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Supported Sheet Types

- Torque Audit Sheet
- Process Audit Check Sheet

Both are Stellantis-specific fixed-template formats only.
