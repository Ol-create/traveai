from traveai.admin import create_key, create_merchant, revoke_key
from traveai.domain.enums import MerchantCategory
from traveai.models import ApiKey


def test_onboard_merchant_and_manage_keys(client, session):
    merchant, test_key = create_merchant(session, "Uptown Pharmacy", MerchantCategory.PHARMACY)
    assert test_key.startswith("sk_test_")
    me = client.get("/v1/me", headers={"Authorization": f"Bearer {test_key}"}).json()
    assert me["merchant_id"] == merchant.id and me["test_mode"] is True

    live_key = create_key(session, merchant.id, live=True)
    assert live_key.startswith("sk_live_")
    live_row = session.query(ApiKey).filter_by(is_test=False).one()
    revoke_key(session, live_row.id)
    assert client.get("/v1/me", headers={"Authorization": f"Bearer {live_key}"}).status_code == 401
