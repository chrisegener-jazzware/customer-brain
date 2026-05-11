FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps for psycopg
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

ENV PYTHONPATH=/app/src

EXPOSE 8000 8502 8503

# Default to API; compose overrides the command for UI containers.
CMD ["uvicorn", "account_intel.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
