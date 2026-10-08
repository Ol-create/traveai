import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from traveai import __version__
from traveai.api import (
    deliveries,
    health,
    map,
    merchants,
    public,
    quotes,
    testing,
    tracking,
    webhooks,
)
from traveai.background import run_in_process
from traveai.config import get_settings
from traveai.openapi import DESCRIPTION, TAGS, operation_id
from traveai.portal import api as portal_api
from traveai.portal import pages as portal_pages
from traveai.workers import SIMULATOR, WEBHOOKS

DASHBOARD_DIR = Path(__file__).parent / "dashboard"
TRACKING_PAGE_DIR = Path(__file__).parent / "tracking_page"
PORTAL_WEB_DIR = Path(__file__).parent / "portal_web"
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
log = logging.getLogger("traveai")


def create_app() -> FastAPI:
    settings = get_settings()
    settings.check_production()  # refuse to start with unsafe production settings

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        tasks = []
        if settings.sim_enabled:
            tasks.append(asyncio.create_task(run_in_process(SIMULATOR, settings)))
        if settings.webhooks_enabled:
            tasks.append(asyncio.create_task(run_in_process(WEBHOOKS, settings)))
        yield
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        lifespan=lifespan,
        title="TraveAI Delivery API",
        summary="Drone delivery for pharmacies and restaurants: quotes, deliveries, live "
        "tracking, webhooks.",
        description=DESCRIPTION,
        version=__version__,
        openapi_tags=TAGS,
        generate_unique_id_function=operation_id,
        debug=settings.debug,
    )

    @app.middleware("http")
    async def request_id_and_errors(request: Request, call_next):
        # Echo a caller's X-Request-Id (sanitised) or make one; it ties logs to responses.
        incoming = request.headers.get("X-Request-Id", "")
        rid = incoming if _SAFE_ID.fullmatch(incoming) else uuid.uuid4().hex
        try:
            response = await call_next(request)
        except Exception:
            log.exception(
                "Unhandled error (request_id=%s %s %s)", rid, request.method, request.url.path
            )
            response = JSONResponse(
                {
                    "detail": {
                        "code": "internal_error",
                        "message": "Something went wrong on our side; quote the request_id "
                        "if you contact support",
                        "request_id": rid,
                    }
                },
                status_code=500,
            )
        response.headers["X-Request-Id"] = rid
        return response

    app.include_router(health.router)
    app.include_router(merchants.router)
    app.include_router(quotes.router)
    app.include_router(deliveries.router)
    app.include_router(tracking.router)
    app.include_router(webhooks.router)
    app.include_router(map.router)

    # Ops dashboard: a static page that calls the API above with the user's key.
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")

    # Merchant portal (/portal): cookie-session JSON API + static web app.
    app.include_router(portal_api.router)
    app.include_router(portal_pages.router)
    app.mount("/portal-assets", StaticFiles(directory=PORTAL_WEB_DIR), name="portal-assets")

    # Public tracking page for recipients (/t/<token>) and its assets.
    app.include_router(public.router)
    app.mount("/track-assets", StaticFiles(directory=TRACKING_PAGE_DIR), name="track-assets")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard/")

    app.include_router(testing.router)
    return app


app = create_app()
