import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from traveai.config import Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False, description="Merchant API key, e.g. sk_test_...")


@dataclass(frozen=True)
class Merchant:
    id: str
    is_test_mode: bool


def require_merchant(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Merchant:
    """Resolve the calling merchant from the `Authorization: Bearer <api_key>` header."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized

    provided = credentials.credentials
    for key, merchant_id in settings.api_key_map.items():
        # Constant-time comparison to avoid timing attacks.
        if secrets.compare_digest(provided, key):
            return Merchant(id=merchant_id, is_test_mode=key.startswith("sk_test_"))
    raise unauthorized


CurrentMerchant = Annotated[Merchant, Depends(require_merchant)]
