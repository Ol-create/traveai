from fastapi import APIRouter
from pydantic import BaseModel

from traveai.auth import CurrentMerchant

router = APIRouter(prefix="/v1", tags=["merchants"])


class MeResponse(BaseModel):
    merchant_id: str
    test_mode: bool


@router.get("/me", response_model=MeResponse)
def me(merchant: CurrentMerchant) -> MeResponse:
    """Return the merchant that owns the API key. Useful to verify a key works."""
    return MeResponse(merchant_id=merchant.id, test_mode=merchant.is_test_mode)
