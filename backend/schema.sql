-- Stellantis Quality Analytics Platform — Database Schema

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Tracks each uploaded PDF and its processing status
CREATE TABLE IF NOT EXISTS uploads (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    original_filename VARCHAR(500) NOT NULL,
    pdf_path         TEXT,
    status           VARCHAR(20) DEFAULT 'PROCESSING',  -- PROCESSING | COMPLETED | FAILED
    total_rows       INTEGER DEFAULT 0,
    processed_pages  INTEGER DEFAULT 0,
    error_message    TEXT,
    created_at       TIMESTAMP DEFAULT NOW(),
    completed_at     TIMESTAMP
);

-- One row per extracted operation from an audit sheet.
-- Analytics MUST ONLY query rows where review_status = 'APPROVED'.
CREATE TABLE IF NOT EXISTS extracted_operations (
    id               SERIAL PRIMARY KEY,
    upload_id        UUID REFERENCES uploads(id) ON DELETE SET NULL,
    audit_date       DATE,
    operation_number VARCHAR(50),
    process_name     TEXT,
    judgement        VARCHAR(10),
    measurements_json JSONB,         -- e.g. [33, 33, 33, 33]
    raw_ocr_json     JSONB,          -- raw Gemini response stored verbatim
    corrected_json   JSONB,          -- user-corrected values stored here
    review_status    VARCHAR(20) DEFAULT 'EXTRACTED',  -- EXTRACTED | REVIEWED | APPROVED | REJECTED
    row_image_path   TEXT,           -- path to the page image shown during review
    reviewed_by      VARCHAR(100),
    reviewed_at      TIMESTAMP,
    created_at       TIMESTAMP DEFAULT NOW()
);

-- Index for fast analytics queries by operation + date range
CREATE INDEX IF NOT EXISTS idx_ops_operation_date
    ON extracted_operations (operation_number, audit_date)
    WHERE review_status = 'APPROVED';

-- Index for review screen queries by upload
CREATE INDEX IF NOT EXISTS idx_ops_upload
    ON extracted_operations (upload_id, review_status);
