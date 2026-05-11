# jazzware-account-intel

Unified per-customer brain. Pulls HubSpot signals (companies + tickets + deals +
**contacts + engagements + quotes + deal stage history**) into a local Postgres
signal store, computes per-company metrics, asks Claude for a roll-up
(`risk_flag` + narrative + **multi-zoom AI summaries**), and exposes two
Streamlit UIs plus a FastAPI service.

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
HubSpot ─► feeder ─► Postgres signal store ─► Claude rollup (cached 6h)
                          │
       ┌──────────────────┼──────────────────┐
       │                  │                  │
  FastAPI service   Internal UI        Client UI
  (8000)            (Streamlit 8502)   (Streamlit 8503)
```

**Phase 2 (this release)** adds contact_signal, activity_signal, quote_signal,
deal stage history, per-company computed metrics, and multi-zoom AI summaries.

---

## Quick demo on localhost

Prereqs: Docker, Docker Compose, your HubSpot PAT and Anthropic key.

```bash
cp .env.example .env
# Edit .env: paste HUBSPOT_TOKEN, ANTHROPIC_API_KEY.
make demo
```

| URL | What it is |
|---|---|
| <http://localhost:8502> | **Internal view** — TL;DR strip, expanded support / sales / contacts / activity / quotes / metrics / hot-signals / integrations tabs, per-tab AI summaries, risk-driver and opportunity bullets. |
| <http://localhost:8503> | **Client Portal** demo — AI welcome TL;DR, service requests with expandable detail, integration health with AI mini-summaries, usage trends with YoY, Your Properties tab (auto-extracted), Insights tab (AI), Roadmap tab, account team panel. |
| <http://localhost:8000/docs> | FastAPI Swagger UI |

### Local dev without Docker

```bash
make install
docker compose up -d db   # or your own Postgres / sqlite
.venv/bin/alembic upgrade head
.venv/bin/python -m account_intel.scripts.seed_demo
# three terminals:
.venv/bin/uvicorn account_intel.api.app:app --reload
.venv/bin/streamlit run src/account_intel/ui/internal_app.py --server.port 8502
.venv/bin/streamlit run src/account_intel/ui/client_app.py   --server.port 8503
```

SQLite also works for local dev (`DATABASE_URL=sqlite:///./account_intel.db`).

---

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/health` | service status |
| GET | `/companies/search?q=...` | search by name/domain |
| GET | `/companies/list` | full directory |
| GET | `/account/{id}` | unified account view (company + tickets + deals + integrations + assessment + multi-zoom summaries) |
| POST | `/account/{id}/refresh` | force HubSpot refresh |
| GET | `/account/{id}/contacts` | associated contacts |
| GET | `/account/{id}/activities?days=90` | engagement timeline (calls/emails/meetings/notes) |
| GET | `/account/{id}/quotes` | quotes (empty if scope denied) |
| GET | `/account/{id}/metrics` | computed metrics (pipeline, win rate, support load, …) |
| GET | `/account/{id}/hot_signals` | auto-surfaced concerns (stalled deals, repeat issues, quiet contacts, old quotes, integration red) |
| GET | `/account/{id}/properties` | properties auto-extracted from deal names (reseller channels) |
| POST | `/account/{id}/refresh_summaries` | recompute multi-zoom AI summaries |

### Multi-zoom AI summaries

`assessment.summaries` carries:

| Key | Audience | Description |
|---|---|---|
| `tldr` | internal | 1-sentence "if you read one thing today" |
| `support_summary` | internal | 2–3 sentences on the support queue |
| `sales_summary` | internal | 2–3 sentences on pipeline health |
| `relationship_summary` | internal | 2–3 sentences on engagement cadence |
| `risk_drivers` | internal | 3–5 specific risk-up bullets |
| `opportunities` | internal | 3–5 expansion / cross-sell bullets |
| `client_tldr` | **client-safe** | 1-sentence portal greeting |
| `client_insights` | **client-safe** | 2–3 sentences on their own trends |

All summaries are cached for 6 hours via the existing `signals_hash` mechanism
(extended to include contacts / activities / quotes / metrics).

---

## Internal UI tabs (port 8502)

1. **TL;DR strip** under the company name — the headline.
2. **AI assessment banner** — risk flag, narrative, model badge.
3. **Risk drivers / Opportunities / Next best actions** — three expanders.
4. **🎫 Support** — sparkline by week + sortable table (with reply counts).
5. **💰 Sales** — pipeline-by-stage chart + sortable table + stage-history expander.
6. **🧑‍💼 Contacts** — title, email, last activity, AI mini-note (active / slowing / quiet).
7. **📅 Activity timeline** — chronological feed of engagements with 24h/7d/30d/90d filter.
8. **📑 Quotes** — title, amount, status, days-to-sign (gracefully empty when scope denied).
9. **📊 Metrics** — full computed-metric grid + properties / sister entities.
10. **🔥 Hot signals** — high/medium/low grouped: stalled deals, aged tickets, repeat-issue clusters, quiet contacts, old quotes, integration red.
11. **🔌 Integrations** — Phase-2 health (still mostly placeholders).
12. **📦 Raw** — full API payload for debugging.

## Client UI tabs (port 8503)

1. **AI welcome card** + KPI strip + status badge.
2. **📋 Service requests** — expandable per-ticket detail (sanitised).
3. **🔌 Integration health** — per-integration AI mini-summary ("Opera PMS has been healthy for 47 consecutive days, …").
4. **📈 Usage trends** — 12-month line chart, YoY metric, peak / hours-saved cards.
5. **🏨 Your properties** *(NEW)* — auto-extracted properties (MGM Macau, Marina Bay Sands, Four Seasons Kyoto…) with per-property status.
6. **💡 Insights** *(NEW)* — AI insight card + recent value events + 12-month activity chart.
7. **🗺️ Roadmap** *(NEW)* — features in pipeline by quarter, with descriptions.
8. **👥 Account team** — CSM / tech lead / exec sponsor + real HubSpot contacts when available + "Schedule a check-in" mailto button.
9. **📊 Quarterly Value Report** — narrative preview.

All internal sales pipeline, AI risk language, and aged-ticket internals are
intentionally hidden from the client view.

---

## Schema (Alembic)

* `20260511_0001_init.py` — company / ticket_signal / deal_signal / integration_signal / ai_assessment
* `20260512_0002_expand_signals.py` — additive only:
  * `company.*` computed metrics: `open_pipeline_amount`, `won_amount_90d`, `lost_amount_90d`, `avg_cycle_days_won`, `win_rate_90d`, `stuck_deals_count`, `support_load_30d`, `first_response_avg_hours`, `repeat_issue_count`, `last_human_activity_at`, `days_since_last_activity`.
  * `ticket_signal.reply_count`, `ticket_signal.first_response_minutes`, `ticket_signal.hubspot_owner_id`.
  * `deal_signal.stage_history_json`, `deal_signal.hubspot_owner_id`.
  * `ai_assessment.summaries_json`.
  * `contact_signal`, `activity_signal`, `quote_signal` (new tables).

Existing rows continue to work; new columns are nullable.

---

## HubSpot scope notes

Verified working on this PAT:
✅ companies, deals, tickets, contacts, calls, meetings, notes (read + associations).

Verified **403 missing-scope** — feeder degrades gracefully (empty arrays, no crash):
* `quotes` — needs `crm.objects.quotes.read`.
* Individual `emails` reads — needs `sales-email-read` / `crm.objects.emails.read`. (Email associations still work — just the per-email property read returns 403, so emails are skipped in the activity feed.)
* `/crm/v3/owners` — needs `crm.objects.owners.read`. (We still store `hubspot_owner_id` from the company property.)

---

## Tests

```bash
make test
```

Unit suite (`tests/`):
* Feeder (mocked HubSpot client) — signal upserts, idempotency, risk score.
* Roll-up — heuristic fallback rubric, hashed cache, multi-zoom summaries shape.
* API — `/health`, `/companies/search`, `/account/{id}`, **plus all new endpoints**: contacts, activities, quotes, metrics, hot_signals, properties, refresh_summaries.
* Property extraction — known-brand fanout, SKU-junk filtering, empty input.

23 tests passing as of `feat/expand-data-and-ai-summaries`.

---

## Deployment

Same as before — see `systemd/` and `Makefile`. No new ports.

---

## Repo layout

```
src/account_intel/
  config.py
  db/
    models.py            # company + ticket_signal + deal_signal + integration_signal +
                         # contact_signal + activity_signal + quote_signal + ai_assessment
    session.py
  feeders/hubspot.py     # JAZ-107 + expansion: contacts + engagements + quotes +
                         # stage history + computed metrics + property extraction
  rollup/
    service.py           # JAZ-108 + multi-zoom summaries
    prompts/system.md
    prompts/summaries.md # multi-zoom prompt
  api/app.py             # all routes
  ui/
    internal_app.py      # internal port 8502
    client_app.py        # client port 8503
    _common.py
  scripts/seed_demo.py
alembic/versions/        # 20260511_0001 + 20260512_0002
tests/
  test_api_unit.py
  test_feeder_unit.py
  test_rollup_unit.py
  test_expansion_unit.py # NEW
```
