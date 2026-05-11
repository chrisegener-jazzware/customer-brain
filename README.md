# jazzware-account-intel

Unified per-customer brain. Pulls HubSpot signals (tickets + deals + companies)
into a local Postgres signal store, asks Claude for a roll-up
(`risk_flag` + narrative + next-best-actions), and exposes two Streamlit UIs
plus a FastAPI service.

Linear project: [`jazzware-account-intel`](https://linear.app/jazzware-automation/project/jazzware-account-intel)
· tickets: [JAZ-105](https://linear.app/jazzware-automation/issue/JAZ-105) (scaffold) ·
[JAZ-106](https://linear.app/jazzware-automation/issue/JAZ-106) (schema) ·
[JAZ-107](https://linear.app/jazzware-automation/issue/JAZ-107) (HubSpot feeder) ·
[JAZ-108](https://linear.app/jazzware-automation/issue/JAZ-108) (Claude roll-up) ·
[JAZ-109](https://linear.app/jazzware-automation/issue/JAZ-109) (internal UI) ·
[JAZ-125](https://linear.app/jazzware-automation/issue/JAZ-125) (client UI demo)

---

## Architecture

```
HubSpot ─► feeder (nightly + on-demand) ─► Postgres signal store
                                                 │
                            ┌────────────────────┼────────────────────┐
                            │                    │                    │
                       FastAPI service     Internal UI          Client UI
                       (8000)              (Streamlit 8502)     (Streamlit 8503)
                                                 │                    │
                                                 ▼                    ▼
                                  search → unified view    "Customer Portal"
                                  AI risk + NBAs           tickets + integrations
                                  HubSpot drill-downs      QVR preview + team
                                                 │
                                                 ▼
                                        Claude roll-up
                                        (cached 6h by signals hash)
```

The roll-up implements the **McLaren correlation pattern**: support fire + stuck
deals = red flag (see `src/account_intel/rollup/prompts/system.md`).

---

## Quick demo on localhost

Prereqs: Docker, Docker Compose, your HubSpot PAT and Anthropic key.

```bash
cp .env.example .env
# Edit .env: paste HUBSPOT_TOKEN (must have tickets+companies+deals scopes)
#           paste ANTHROPIC_API_KEY (optional — heuristic fallback runs without it)
#           DEMO_COMPANY_IDS already points to the two McLaren accounts

make demo
```

`make demo` brings up Postgres + API + both UIs, runs Alembic migrations, and
pre-seeds the demo HubSpot companies. When it finishes you have:

| URL | What it is |
|---|---|
| <http://localhost:8502> | **Internal view** — search any HubSpot company, full McLaren-style report with sales, support, AI assessment, next-best-actions, HubSpot drill-down links. (JAZ-109) |
| <http://localhost:8503> | **Client Portal** demo — same data, filtered to a "what a customer would see": their tickets, integration health, usage trends, account team, Quarterly Value Report preview. Internal sales + AI risk are hidden. (JAZ-125) |
| <http://localhost:8000/docs> | FastAPI Swagger UI |

To run them side-by-side in your browser:

```bash
# Tab 1: internal
open http://localhost:8502
# Tab 2: client portal — pick a customer from the "Logged in as" dropdown
open http://localhost:8503
```

Both Streamlit apps read from the **same Postgres signal store** via the FastAPI
service, so any data you refresh in the internal view shows up immediately in
the client view.

### To pre-load more demo accounts

Add their HubSpot company ids (comma-separated) to `DEMO_COMPANY_IDS` in `.env`,
then:

```bash
make seed
```

---

## Local dev (no Docker)

```bash
make install                    # creates .venv and installs deps
# start a postgres yourself, or:  docker compose up -d db
export DATABASE_URL='postgresql+psycopg://account_intel:account_intel@localhost:5433/account_intel'
.venv/bin/alembic upgrade head
.venv/bin/python -m account_intel.scripts.seed_demo
# in three terminals:
.venv/bin/uvicorn account_intel.api.app:app --reload
.venv/bin/streamlit run src/account_intel/ui/internal_app.py --server.port 8502
.venv/bin/streamlit run src/account_intel/ui/client_app.py   --server.port 8503
```

---

## Tests

```bash
make test
```

Unit suite covers:
- **Feeder** — HubSpot client mocked; verifies signal upserts, idempotency, McLaren risk score.
- **Roll-up** — heuristic fallback rubric, signal-hash caching, payload assembly.
- **API** — `/health`, `/companies/search`, `/account/{id}`, 404 behavior.

CI runs ruff + pytest on every push and PR (`.github/workflows/ci.yml`).

---

## Deployment (VM2)

Deploys alongside `hotel-info-lookup` (which uses 8501). Ports here are 8000
(API), 8502 (internal UI), 8503 (client UI). systemd units in `systemd/`:

```bash
sudo cp systemd/account-intel-*.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now account-intel-api account-intel-ui-internal account-intel-ui-client
sudo systemctl enable --now account-intel-feeder.timer   # nightly 03:30
```

---

## Repo layout

```
src/account_intel/
  config.py              # pydantic-settings
  db/                    # SQLAlchemy models + session
  feeders/hubspot.py     # JAZ-107 — port of /tmp/account_intel_full.py PoC
  rollup/
    service.py           # JAZ-108 — Claude roll-up, hashed cache, fallback
    prompts/system.md    # rubric + McLaren few-shot
  api/app.py             # JAZ — FastAPI: search + account view + refresh
  ui/
    internal_app.py      # JAZ-109 — port 8502
    client_app.py        # JAZ-125 — port 8503
  scripts/seed_demo.py   # pre-load demo HubSpot companies
alembic/                 # JAZ-106 — versioned schema
tests/                   # unit suite (mocked HubSpot + in-memory DB)
systemd/                 # production units
Dockerfile, docker-compose.yml, Makefile
```
