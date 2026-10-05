from fastapi import FastAPI

from traveai import __version__
from traveai.api import health, merchants
from traveai.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="TraveAI Delivery API",
        description="Drone delivery API for merchants: quotes, deliveries, live tracking.",
        version=__version__,
        debug=settings.debug,
    )
    app.include_router(health.router)
    app.include_router(merchants.router)
    return app


app = create_app()
