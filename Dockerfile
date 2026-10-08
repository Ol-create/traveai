# One image for the API, the workers and migrations (the command decides which).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
RUN useradd --create-home --uid 10001 traveai

# Dependencies first, for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[postgres,redis]"

COPY alembic.ini ./
COPY migrations ./migrations

USER traveai
EXPOSE 8000

# --proxy-headers: trust X-Forwarded-* from the load balancer in front of the API.
CMD ["uvicorn", "traveai.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips=*"]
