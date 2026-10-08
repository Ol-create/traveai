"""Run a background worker as its own process.

    python -m traveai.worker simulator
    python -m traveai.worker webhooks

Run two of each for failover: only one is active at a time (see traveai.workers).
Stops cleanly on Ctrl+C or SIGTERM (e.g. `docker stop`), handing its lease to a standby.
"""

import argparse
import logging
import signal
import threading
import time

from traveai.config import get_settings
from traveai.db import get_sessionmaker
from traveai.workers import TICK_SECONDS, WORKERS, make_job, new_holder_id, release, tick

log = logging.getLogger("traveai.worker")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="TraveAI background worker")
    parser.add_argument("name", choices=WORKERS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = get_settings()
    settings.check_production()
    sessions = get_sessionmaker()
    holder = new_holder_id()
    job = make_job(args.name, settings)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    log.info("%s worker %s started", args.name, holder)
    active = False
    while not stop.is_set():
        started = time.monotonic()
        try:
            now_active = tick(sessions, args.name, holder, job)
            if now_active != active:
                log.info("%s: %s", holder, "now active" if now_active else "standing by")
                active = now_active
        except Exception:  # keep running; the next tick retries
            log.exception("%s tick failed", args.name)
        stop.wait(max(0.0, TICK_SECONDS - (time.monotonic() - started)))

    with sessions() as session:
        release(session, args.name, holder)
    log.info("%s worker %s stopped", args.name, holder)


if __name__ == "__main__":
    main()
