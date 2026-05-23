import uuid
from sqlalchemy import Column, Integer, String, Text, Date, DateTime, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database import Base


class Upload(Base):
    __tablename__ = "uploads"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_filename = Column(String(500), nullable=False)
    pdf_path = Column(Text)
    status = Column(String(20), default="PROCESSING")  # PROCESSING | COMPLETED | FAILED
    total_rows = Column(Integer, default=0)
    processed_pages = Column(Integer, default=0)
    error_message = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime)


class ExtractedOperation(Base):
    __tablename__ = "extracted_operations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    upload_id = Column(UUID(as_uuid=True), nullable=True)
    audit_date = Column(Date, nullable=True)
    operation_number = Column(String(50))
    engine_number = Column(String(50))
    process_name = Column(Text)
    quantity = Column(Integer)
    judgement = Column(String(10))
    measurements_json = Column(JSONB)   # e.g. [33, 33, 33, 33]
    raw_ocr_json = Column(JSONB)        # verbatim Gemini response
    corrected_json = Column(JSONB)      # user corrections
    review_status = Column(String(20), default="EXTRACTED")
    row_image_path = Column(Text)
    reviewed_by = Column(String(100))
    reviewed_at = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())
    # Dual-model comparison fields
    gemini_raw = Column(JSONB)
    gpt4o_raw = Column(JSONB)
    agreement_score = Column(Integer, default=0)
    disagreements = Column(JSONB)
