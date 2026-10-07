"""Runs the webhook outbox in the background of the API process."""

import asyncio
import logging

from traveai.db import get_sessionmaker
from traveai.models.base import utcnow
from traveai.webhooks.sender import fan_out, http_transport, send_due

log = logging.getLogger(__name__)
TICK_SECONDS = 1.0


def _step_once() -> None:
    with get_sessionmaker()() as session:
        now = utcnow()
        fan_out(session, now)
        session.commit()
        send_due(session, now, http_transport)


async def run_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(_step_once)  # blocking DB + HTTP calls
        except Exception:
            log.exception("Webhook step failed")
        await asyncio.sleep(TICK_SECONDS)
