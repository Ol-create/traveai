from logging.config import fileConfig

from alembic import context

from traveai.config import get_settings
from traveai.db import make_engine
from traveai.models import Base

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    # An explicit URL (e.g. set by tests) wins over the app settings.
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = get_url()
    # On SQLite, batch mode rebuilds a changed table (copy, drop, rename). With foreign keys
    # enforced, dropping a table that other rows point at fails, so migrate with enforcement
    # off and check integrity afterwards. (Postgres alters tables in place; unaffected.)
    engine = make_engine(url, enforce_foreign_keys=False)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
        if url.startswith("sqlite"):
            broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise RuntimeError(f"Migration left broken foreign keys: {broken[:5]}")
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
