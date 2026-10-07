import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI

from traveai import __version__
from traveai.api import deliveries, health, merchants, quotes, testing
from traveai.config import get_settings
from traveai.sim.runner import run_forever


def create_app() -> FastAPI:
    settings = get_settings()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(run_forever(settings)) if settings.sim_enabled else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(
        lifespan=lifespan,
        title="TraveAI Delivery API",
        description="Drone delivery API for merchants: quotes, deliveries, live tracking.",
        version=__version__,
        debug=settings.debug,
    )
    app.include_router(health.router)
    app.include_router(merchants.router)
    app.include_router(quotes.router)
    app.include_router(deliveries.router)
    app.include_router(testing.router)
    return app


app = create_app()
