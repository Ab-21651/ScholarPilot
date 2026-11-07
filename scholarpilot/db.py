"""Database engine and session helpers (SQLite via SQLAlchemy)."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session for scripts and background jobs; commits on success."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _add_missing_columns() -> None:
    """Tiny migration step: add columns that exist in the models but not yet in an older SQLite file."""
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table.name})")}
            for col in table.columns:
                if col.name not in existing:
                    col_type = col.type.compile(dialect=engine.dialect)
                    default = ""
                    if col.default is not None and getattr(col.default, "is_scalar", False):
                        default = f" DEFAULT {col.default.arg!r}"
                    conn.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}{default}")


def init_db() -> None:
    from . import models  # noqa: F401  (registers tables)
    from .catalog import seed_catalog

    Base.metadata.create_all(engine)
    _add_missing_columns()
    with session_scope() as db:
        seed_catalog(db)
