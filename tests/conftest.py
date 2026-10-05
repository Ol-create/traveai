import pytest
from fastapi.testclient import TestClient

from traveai.config import Settings, get_settings
from traveai.main import create_app

TEST_KEY = "sk_test_unit_0000"
TEST_MERCHANT = "merch_unit_test"


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, api_keys=f"{TEST_KEY}:{TEST_MERCHANT}"
    )
    return TestClient(app)
