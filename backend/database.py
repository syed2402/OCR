import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH, override=True)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://quality_user:quality_pass@localhost:5432/stellantis_quality",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables defined in models. Call at startup."""
    from models import Base as ModelBase  # noqa: F401 — ensures models are registered
    ModelBase.metadata.create_all(bind=engine)
