from fastapi import APIRouter

from traveai import __version__

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Public liveness check."""
    return {"status": "ok", "version": __version__}
