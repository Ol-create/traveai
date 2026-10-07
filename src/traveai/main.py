import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from traveai import __version__
from traveai.api import (
    deliveries,
    health,
    map,
    merchants,
    quotes,
    testing,
    tracking,
    webhooks,
)
from traveai.config import get_settings
from traveai.openapi import DESCRIPTION, TAGS, operation_id
from traveai.sim.runner import run_forever as run_simulator
from traveai.webhooks.runner import run_forever as run_webhooks

DASHBOARD_DIR = Path(__file__).parent / "dashboard"


def create_app() -> FastAPI:
    settings = get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        tasks = []
        if settings.sim_enabled:
            tasks.append(asyncio.create_task(run_simulator(settings)))
        if settings.webhooks_enabled:
            tasks.append(asyncio.create_task(run_webhooks()))
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
    app.include_router(health.router)
    app.include_router(merchants.router)
    app.include_router(quotes.router)
    app.include_router(deliveries.router)
    app.include_router(tracking.router)
    app.include_router(webhooks.router)
    app.include_router(map.router)

    # Ops dashboard: a static page that calls the API above with the user's key.
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard/")

    app.include_router(testing.router)
    return app


app = create_app()
