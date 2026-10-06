from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from traveai.db import get_session, make_engine
from traveai.domain.enums import MerchantCategory
from traveai.main import create_app
from traveai.models import Base, Merchant

TEST_MERCHANT_ID = "merch_unit_test"


@pytest.fixture
def session() -> Iterator[Session]:
    """Fresh in-memory database per test."""
    engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s
    engine.dispose()


@pytest.fixture
def merchant(session: Session) -> Merchant:
    m = Merchant(id=TEST_MERCHANT_ID, name="Unit Test Pharmacy", category=MerchantCategory.PHARMACY)
    session.add(m)
    session.commit()
    return m


@pytest.fixture
def api_key(session: Session, merchant: Merchant) -> str:
    key = merchant.issue_api_key(test=True)
    session.commit()
    return key


@pytest.fixture
def client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)
