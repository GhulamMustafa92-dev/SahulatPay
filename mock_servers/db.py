"""Shared SQLite engine for all mock servers. Sync SQLAlchemy — no async needed for mocks."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

MOCK_DB_PATH = os.getenv("MOCK_DB_PATH", "./mock.db")
MOCK_DB_URL  = f"sqlite:///{MOCK_DB_PATH}"

engine = create_engine(
    MOCK_DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def create_all_tables():
    from mock_servers import models  # noqa: F401 — ensure models are registered
    Base.metadata.create_all(bind=engine)
