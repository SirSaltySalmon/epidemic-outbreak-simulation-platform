from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from eosp.core.settings import get_settings


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session] | None:
    url = database_url or get_settings().database_url
    if not url:
        return None
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
