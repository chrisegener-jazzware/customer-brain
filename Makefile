.PHONY: help install dev fmt lint test up down logs migrate seed demo

PY ?= python3
VENV ?= .venv

help:
	@echo "Targets:"
	@echo "  install   - create venv and install deps"
	@echo "  dev       - run API + both UIs locally against docker Postgres"
	@echo "  test      - run pytest"
	@echo "  fmt lint  - ruff format / lint"
	@echo "  up        - docker compose up -d (db + api + ui-internal + ui-client)"
	@echo "  down      - docker compose down"
	@echo "  migrate   - alembic upgrade head"
	@echo "  seed      - pre-load demo HubSpot companies"
	@echo "  demo      - up + migrate + seed → http://localhost:8502 + 8503"

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -e ".[dev]"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

migrate:
	docker compose exec api alembic upgrade head

seed:
	docker compose exec api python -m account_intel.scripts.seed_demo

demo: up
	@echo "Waiting for API healthcheck..."
	@for i in $$(seq 1 30); do curl -fsS http://localhost:8000/health >/dev/null 2>&1 && break || sleep 1; done
	$(MAKE) seed
	@echo ""
	@echo "✅ Demo ready"
	@echo "   Internal UI:  http://localhost:8502   (JAZ-109)"
	@echo "   Client UI:    http://localhost:8503   (JAZ-125)"
	@echo "   API:          http://localhost:8000/docs"

test:
	$(VENV)/bin/pytest -v

fmt:
	$(VENV)/bin/ruff format src tests

lint:
	$(VENV)/bin/ruff check src tests
