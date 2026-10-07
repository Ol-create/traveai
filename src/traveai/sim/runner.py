"""Runs the simulator in the background of the API process."""

import asyncio
import logging
import random

from traveai.config import Settings
from traveai.db import get_sessionmaker
from traveai.models.base import utcnow
from traveai.rules.airspace import default_airspace
from traveai.rules.config import RulesConfig
from traveai.rules.weather import SimulatedWeatherProvider
from traveai.sim.simulator import SimContext, Simulator

log = logging.getLogger(__name__)


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


def _step_once(sim: Simulator, dt_s: float) -> None:
    with get_sessionmaker()() as session:
        sim.step(session, utcnow(), dt_s)


async def run_forever(settings: Settings) -> None:
    sim = build_simulator(settings)
    dt_s = settings.sim_tick_seconds * settings.sim_speed
    log.info(
        "Simulator running: tick %.1fs, speed %.0fx", settings.sim_tick_seconds, settings.sim_speed
    )
    while True:
        try:
            # The simulator uses blocking DB calls; keep them off the event loop.
            await asyncio.to_thread(_step_once, sim, dt_s)
        except Exception:  # keep flying even if one step hits a bad row
            log.exception("Simulator step failed")
        await asyncio.sleep(settings.sim_tick_seconds)
