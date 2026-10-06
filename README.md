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
