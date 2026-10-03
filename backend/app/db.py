from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

if settings.is_postgres:
    engine = create_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,  # Neon/Supabase close idle connections
        pool_size=5,
        max_overflow=5,
        connect_args={"prepare_threshold": None},  # safe behind PgBouncer poolers
    )
else:
    engine = create_engine(
        settings.sqlalchemy_url, connect_args={"check_same_thread": False, "timeout": 30}
    )

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    """For agents/background work: commits on success, rolls back on error."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
