from fastapi import APIRouter
from pydantic import BaseModel

from traveai.auth import CurrentAuth
from traveai.domain.enums import MerchantCategory
from traveai.openapi import errors

router = APIRouter(prefix="/v1", tags=["merchants"], responses=errors(401, 429))


class MeResponse(BaseModel):
    merchant_id: str
    name: str
    category: MerchantCategory
    test_mode: bool


@router.get("/me", response_model=MeResponse, summary="Your account")
def me(auth: CurrentAuth) -> MeResponse:
    """Return the merchant that owns the API key. Useful to verify a key works."""
    return MeResponse(
        merchant_id=auth.merchant.id,
        name=auth.merchant.name,
        category=auth.merchant.category,
        test_mode=auth.test_mode,
    )
