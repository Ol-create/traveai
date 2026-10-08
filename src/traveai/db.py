from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from traveai.config import get_settings


def make_engine(url: str, *, enforce_foreign_keys: bool = True, **kwargs) -> Engine:
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        kwargs.setdefault("connect_args", {"check_same_thread": False})
    engine = create_engine(url, **kwargs)
    if is_sqlite and enforce_foreign_keys:
        # SQLite ignores foreign keys unless asked per connection.
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one DB session per request."""
    with get_sessionmaker()() as session:
        yield session
