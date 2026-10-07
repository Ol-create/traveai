# TraveAI

Drone delivery API for merchants (pharmacies and restaurants) in the US, backed by a flight
simulator. Pure software for now: no real drones.

## Stack

Python 3.11+, FastAPI, Uvicorn, Pydantic, SQLAlchemy + Alembic (SQLite for now).
Tests with pytest, linting with Ruff.

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash; on macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head        # create the database tables
python -m traveai.seed      # demo merchants, API keys, Dallas drones and drop zones
```

## Run

```bash
uvicorn traveai.main:app --reload
```

- API docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Authentication

Send your API key as a bearer token. Test keys are in `.env.example` (loaded into the DB by the
seed script; only SHA-256 hashes are stored).

```bash
curl -H "Authorization: Bearer <your sk_test_ key>" http://127.0.0.1:8000/v1/me
```

## Quotes

`POST /v1/quotes` checks and prices a delivery before booking it. It always returns a quote; if
the delivery can't happen it has `feasible: false` and lists every reason.

```bash
curl -X POST http://127.0.0.1:8000/v1/quotes \
  -H "Authorization: Bearer <your sk_test_ key>" -H "Content-Type: application/json" \
  -d '{"pickup": {"lat": 32.7843, "lng": -96.7837},
       "dropoff": {"lat": 32.8120, "lng": -96.7520},
       "payload": {"category": "food", "weight_kg": 1.2,
                   "length_cm": 30, "width_cm": 25, "height_cm": 15}}'
```

How a quote is built:

1. Every drone in service plans the full loop: hub → pickup → drop-off → hub.
2. Each plan is checked against the flight rules, the weather (simulated, repeatable) at pickup
   and drop-off, and the drone's battery range (with a 20% reserve).
3. The earliest feasible drop-off wins. If none is feasible, the reasons come from the plan
   closest to working.
4. Price = $4.99 base + $1.20/km + $0.75 per started 0.5 kg over 1 kg + $3.00 cold chain +
   priority surcharge (express $3.99, urgent $9.99, medical only).

Quotes expire after 5 minutes. `GET /v1/quotes/{id}` fetches one of your own quotes.
Real clock time applies, so quotes made after dark in Dallas come back `outside_daylight`.

## Deliveries

| Endpoint | What it does |
|---|---|
| `POST /v1/deliveries` | Book a feasible, unexpired quote (`quote_id`). Each quote books once, so retries can't double-book. Prescriptions return a one-time `recipient_pin`. |
| `GET /v1/deliveries` | Your deliveries, newest first. Filters: `status` (repeatable), `external_reference`, `created_gte`, `created_lt`. Paginate with `limit` + `starting_after`. |
| `GET /v1/deliveries/{id}` | One delivery, including `proof` once delivered. |
| `GET /v1/deliveries/{id}/events` | Timeline: status changes, chain-of-custody hand-offs, failed PIN attempts. |
| `POST /v1/deliveries/{id}/cancel` | Cancel before takeoff (`409 cannot_cancel` once airborne). |
| `GET /v1/deliveries/{id}/proof.svg` | Simulated drop-off photo. |
| `POST /v1/test/deliveries/{id}/advance` | **Test keys only.** Move a delivery one step (or `to` a status), standing in for the flight simulator. Delivering checks `pin`. |

Errors have a stable code: `{"detail": {"code": "quote_expired", "message": "..."}}`.

Recipient PINs are stored as salted PBKDF2 hashes; 5 wrong attempts lock the drop-off.

## Flight simulator

With `TRAVEAI_SIM_ENABLED=true` the API server also runs the simulator in the background
(`TRAVEAI_SIM_SPEED=10` flies drones 10x faster than real time). Each tick it:

1. **Dispatches** idle (or charged-enough) drones to scheduled deliveries, urgent first, after a
   pre-flight check with live weather, airspace and the drone's current battery. Deliveries no
   drone can fly are held (weather may clear) and fail after an hour.
2. **Flies** each mission `hub -> pickup -> drop-off -> hub` along a route planned **around**
   active no-fly zones (150 m margin), draining battery per meter flown, and moves the delivery
   through `assigned -> picking_up -> airborne -> arriving -> delivered` on its own.
3. **Charges** drones back at their hub (empty to full in 30 min).

Prescriptions: the drone hovers at the drop-off for up to 2 minutes (budgeted in its range)
until `POST /v1/deliveries/{id}/handoff` sends the recipient's PIN.

Failures: wind gusts over a drone's limit (recall before pickup, abort after), low battery,
blocked drop zone, recipient unavailable. An aborted delivery flies home with the package and
is retried once, then fails. `TRAVEAI_SIM_FAILURE_RATE` adds random failures; in test mode,
`POST /v1/test/deliveries/{id}/failures {"kind": "high_wind" | "low_battery" | "drop_zone_blocked"}`
forces one.

Flying after dark needs `TRAVEAI_ALLOW_NIGHT_OPERATIONS=true` (Part 107 night rules).

## Test and lint

```bash
pytest
ruff check .
```

## Project layout

```
src/traveai/
  main.py        app factory and router registration
  config.py      settings from env / .env
  db.py          database engine and per-request sessions
  auth.py        API-key authentication (DB lookup by key hash)
  seed.py        demo data for local development
  api/           route modules
  domain/        enums and the delivery status state machine
  schemas/       validated value objects (Payload, Location)
  rules/         US flight rules engine: airspace, LAANC mock, daylight, Part 107, payload rules
  data/          bundled airspace GeoJSON
  models/        database tables: merchants, api_keys, vehicles, drop_zones,
                 quotes, deliveries, missions, events
migrations/      Alembic migrations
tests/           pytest suite
```

## Database changes

After changing a model, generate and apply a migration:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

`tests/test_migrations.py` fails if the models and migrations drift apart.

## Delivery lifecycle

```
Happy path:    scheduled -> assigned -> picking_up -> airborne -> arriving -> delivered
Before flight: scheduled / assigned / picking_up -> canceled
In the air:    airborne / arriving -> aborted -> returned_to_base -> scheduled (retry)
Give up:       any non-final status -> failed
```

Every status change records an `Event` (e.g. `delivery.airborne`), which will drive webhooks.

## Flight rules (simulated US compliance)

`traveai.rules.engine.evaluate(FlightRequest)` checks a proposed flight and returns **every**
problem at once as machine-readable codes (e.g. `laanc_denied`, `food_delivery_too_slow`), plus
requirements such as `recipient_pin` and the approved cruise altitude.

| Area | Rule |
|---|---|
| Part 107 | Max 400 ft altitude, max 100 mph, payload within drone capacity, daylight or civil twilight only |
| No-fly zones | Restricted areas always; stadium TFRs during events (including ones starting mid-flight) |
| Airports | Mock LAANC: approved at the grid ceiling (e.g. 100 ft near Love Field), denied if under 100 ft |
| Medical | Temperature-controlled drone for cold-chain items, chain-of-custody log, recipient PIN for prescriptions, only medical may use `urgent` priority |
| Food | Max 30 min flight so it arrives hot |

Airspace data is in `src/traveai/data/airspace/dallas.geojson`. It is **demo data, not for real
flight planning**: airport positions are real, but LAANC ceilings, stadium event dates and the
restricted area are made up.
