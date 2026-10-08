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


def test_migrations_work_with_data_in_place(tmp_path):
    """SQLite rebuilds tables to alter them; with rows pointing at `deliveries` that used to
    fail on a foreign key. Down- and up-grade across a deliveries change with data present."""
    from sqlalchemy.orm import Session

    from traveai.domain.enums import MerchantCategory, PayloadCategory
    from traveai.models import Delivery, Merchant
    from traveai.schemas.location import Location
    from traveai.schemas.payload import Payload

    url = f"sqlite:///{tmp_path / 'data.db'}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")

    engine = make_engine(url)
    with Session(engine) as s:
        m = Merchant(name="M", category=MerchantCategory.PHARMACY)
        d = Delivery(merchant=m, price_cents=100)
        d.pickup, d.dropoff = Location(lat=32.78, lng=-96.78), Location(lat=32.81, lng=-96.75)
        d.payload = Payload(
            category=PayloadCategory.FOOD, weight_kg=1, length_cm=1, width_cm=1, height_cm=1
        )
        s.add_all([m, d])
        s.flush()
        d.transition_to(d.status.ASSIGNED)  # an event row referencing the delivery
        s.commit()
    engine.dispose()

    command.downgrade(cfg, "3ee445b5c0d2")  # rebuilds `deliveries`
    command.upgrade(cfg, "head")  # and again, backfilling a tracking token
    engine = make_engine(url)
    with Session(engine) as s:
        assert s.query(Delivery).one().tracking_token
    engine.dispose()
