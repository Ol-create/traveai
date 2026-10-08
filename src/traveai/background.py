"""Run background workers inside the API process (development / single-server setups).

Uses the same leases as `python -m traveai.worker`, so it is safe to also run separate
workers or several API replicas: only one copy of each job is active.
"""

import asyncio
import logging

from traveai.config import Settings
from traveai.db import get_sessionmaker
from traveai.workers import TICK_SECONDS, make_job, new_holder_id, release, tick

log = logging.getLogger(__name__)


async def run_in_process(name: str, settings: Settings) -> None:
    sessions = get_sessionmaker()
    holder = new_holder_id()
    job = make_job(name, settings)
    try:
        while True:
            try:
                # Blocking DB and HTTP calls: keep them off the event loop.
                await asyncio.to_thread(tick, sessions, name, holder, job)
            except Exception:
                log.exception("%s tick failed", name)
            await asyncio.sleep(TICK_SECONDS)
    finally:
        with sessions() as session:
            release(session, name, holder)
