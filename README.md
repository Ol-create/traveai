# TraveAI

Drone delivery API for merchants (pharmacies and restaurants) in the US, backed by a flight
simulator. Pure software for now: no real drones.

## Stack

Python 3.11+, FastAPI, Uvicorn, Pydantic. Tests with pytest, linting with Ruff.

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash; on macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

## Run

```bash
uvicorn traveai.main:app --reload
```

- API docs: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

## Authentication

Send your API key as a bearer token. Test keys are in `.env.example`.

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
  auth.py        API-key authentication
  api/           route modules
tests/           pytest suite
```
