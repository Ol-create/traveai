from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from traveai import __version__
from traveai.db import get_session
from traveai.deps import get_now
from traveai.workers import WORKERS, lease_alive

router = APIRouter(tags=["health"])


@router.get("/health", summary="Health check")
def health() -> dict[str, str]:
    """Liveness: the process is up. Cheap; doesn't touch the database."""
    return {"status": "ok", "version": __version__}


@router.get(
    "/ready",
    summary="Readiness check",
    responses={503: {"description": "Database unreachable"}},
)
def ready(
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(get_now)],
) -> JSONResponse:
    """Readiness: the database answers. Also reports which background workers are active
    (informational: the API keeps serving while a worker restarts)."""
    try:
        session.execute(text("SELECT 1"))
        workers = {
            name: "active" if lease_alive(session, name, now) else "absent" for name in WORKERS
        }
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ready", "database": "ok", "workers": workers})
