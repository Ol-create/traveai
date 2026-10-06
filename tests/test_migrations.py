from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from traveai.db import make_engine
from traveai.models import Base

ROOT = Path(__file__).resolve().parents[1]


def test_migrations_match_models(tmp_path):
    """Upgrading an empty DB to head must produce exactly the schema the models describe.
    Fails if someone changes a model without generating a migration."""
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False

    command.upgrade(cfg, "head")

    engine = make_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []

    command.downgrade(cfg, "base")
