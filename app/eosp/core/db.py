from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from eosp.core.settings import get_settings


def ensure_psycopg3_driver(url: str) -> str:
    """Use psycopg v3; bare ``postgresql://`` / ``postgres://`` defaults to psycopg2 in SQLAlchemy."""
    scheme, sep, rest = url.partition("://")
    if not sep or "+" in scheme:
        return url
    if scheme in ("postgresql", "postgres"):
        return f"postgresql+psycopg://{rest}"
    return url


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session] | None:
    raw = database_url or get_settings().database_url
    if not raw:
        return None
    url = ensure_psycopg3_driver(raw)
    connect_args = {"connect_timeout": 2} if url.startswith("postgresql") else {}
    engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
    return sessionmaker(bind=engine, expire_on_commit=False)


def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
