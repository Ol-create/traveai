"""Background workers: the flight simulator and the webhook sender.

Run each as its own process in production:

    python -m traveai.worker simulator
    python -m traveai.worker webhooks

(or inside the API process for development, with TRAVEAI_SIM_ENABLED / TRAVEAI_WEBHOOKS_ENABLED).

Any number of copies may run. Each tick a worker renews a lease in the database; only the
lease holder does the work, and a standby takes over within LEASE_TTL if the holder dies.
"""

import logging
import os
import random
import socket
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from traveai.config import Settings
from traveai.models.base import utcnow
from traveai.models.ops import RuntimeSetting, WorkerLease
from traveai.rules.airspace import default_airspace
from traveai.rules.config import RulesConfig
from traveai.rules.weather import SimulatedWeatherProvider
from traveai.sim.simulator import SimContext, Simulator
from traveai.webhooks.sender import fan_out, http_transport, send_due

log = logging.getLogger(__name__)

SIMULATOR, WEBHOOKS = "simulator", "webhooks"
WORKERS = (SIMULATOR, WEBHOOKS)
LEASE_TTL = timedelta(seconds=10)
TICK_SECONDS = 1.0

MIN_SPEED, MAX_SPEED = 1.0, 100.0
SPEED_KEY = "sim.speed"


def new_holder_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


# --- leases ---------------------------------------------------------------------------------


def try_acquire(session: Session, name: str, holder: str, now: datetime) -> bool:
    """Take or renew the lease. True if `holder` may do the work until the next tick."""
    # FOR UPDATE: on Postgres, two workers can't both grab an expired lease (no-op on SQLite).
    lease = session.get(WorkerLease, name, with_for_update=True)
    if lease is None:
        session.add(WorkerLease(name=name, holder=holder, expires_at=now + LEASE_TTL))
        try:
            session.commit()
        except IntegrityError:  # another worker created it first
            session.rollback()
            return False
        return True
    if lease.holder != holder and lease.expires_at > now:
        session.rollback()
        return False
    lease.holder, lease.expires_at = holder, now + LEASE_TTL
    session.commit()
    return True


def release(session: Session, name: str, holder: str) -> None:
    lease = session.get(WorkerLease, name)
    if lease is not None and lease.holder == holder:
        session.delete(lease)  # lets a standby take over immediately
        session.commit()


def lease_alive(session: Session, name: str, now: datetime) -> bool:
    lease = session.get(WorkerLease, name)
    return lease is not None and lease.expires_at > now


# --- runtime settings -----------------------------------------------------------------------


def get_runtime(session: Session, key: str, default: Any) -> Any:
    row = session.get(RuntimeSetting, key)
    return default if row is None else row.value


def set_runtime(session: Session, key: str, value: Any) -> None:
    row = session.get(RuntimeSetting, key)
    if row is None:
        session.add(RuntimeSetting(key=key, value=value))
    else:
        row.value = value
    session.commit()


def sim_speed(session: Session, settings: Settings) -> float:
    speed = float(get_runtime(session, SPEED_KEY, settings.sim_speed))
    return min(MAX_SPEED, max(MIN_SPEED, speed))


# --- the jobs -------------------------------------------------------------------------------


def build_simulator(settings: Settings) -> Simulator:
    return Simulator(
        SimContext(
            weather=SimulatedWeatherProvider(),
            airspace=default_airspace(),
            config=RulesConfig(allow_night_operations=settings.allow_night_operations),
            failure_rate=settings.sim_failure_rate,
            rng=random.Random(),
        )
    )


def make_job(name: str, settings: Settings) -> Callable[[Session, datetime], None]:
    """One tick of work for `name`, given a session and the current time."""
    if name == SIMULATOR:
        sim = build_simulator(settings)

        def simulate(session: Session, now: datetime) -> None:
            dt_s = settings.sim_tick_seconds * sim_speed(session, settings)
            sim.step(session, now, dt_s)

        return simulate
    if name == WEBHOOKS:

        def send(session: Session, now: datetime) -> None:
            fan_out(session, now)
            session.commit()
            send_due(session, now, http_transport)

        return send
    raise ValueError(f"unknown worker {name!r}; choose from {WORKERS}")


def tick(
    session_factory: Callable[[], Session],
    name: str,
    holder: str,
    job: Callable[[Session, datetime], None],
    now: datetime | None = None,
) -> bool:
    """Run one tick if we hold the lease. Returns whether we did the work."""
    now = now or utcnow()
    with session_factory() as session:
        if not try_acquire(session, name, holder, now):
            return False
        job(session, now)
        return True
