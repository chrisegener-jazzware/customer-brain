# Customer Brain — HubSpot UI Extension (JAZ-110)

Private HubSpot app that surfaces the customer-brain score, narrative, and top-3
next-best-actions on the company record sidebar. "See full view" links to the
internal Streamlit dashboard.

## What it ships

- `crm.company.sidebar` card titled **Customer Brain**
- Serverless function `fetchAccount` proxies to the customer-brain service
- Falls back to `sample-account.json` when no backend URL is set so devs can
  preview the card without spinning up the API.

## Repo layout

```
hubspot-ui/
├─ hsproject.json
├─ package.json
├─ src/app/app.json
└─ src/app/
   ├─ extensions/
   │   ├─ CustomerBrainCard.json   # card config (sidebar, companies)
   │   └─ CustomerBrainCard.jsx    # React component
   └─ app.functions/
       ├─ serverless.json          # function manifest + required secrets
       ├─ fetchAccount.js          # proxy to customer-brain
       └─ sample-account.json      # dev fixture
```

## Required scopes

- `crm.objects.companies.read`
- `crm.schemas.companies.read`

## Secrets

Set in HubSpot developer portal (Project → Settings → Secrets):

| Name | Purpose | Required |
|---|---|---|
| `CUSTOMER_BRAIN_URL` | Base URL of the customer-brain FastAPI service (e.g. `https://customer-brain.jazzware.internal`) | Yes for prod |
| `CUSTOMER_BRAIN_API_KEY` | Bearer token for the service if it enforces auth | Optional |
| `STREAMLIT_BASE_URL` | Base URL of the internal Streamlit UI (`See full view`) | Optional |

When `CUSTOMER_BRAIN_URL` is unset the function serves `sample-account.json`
so the card renders in dev/preview mode.

## Backend contract

The serverless function calls two endpoints on the customer-brain service:

- `GET /account/{company_id}` — provided by `account_intel.api.app`
- `GET /health/score/{company_id}?use_claude=false` — provided by the
  `health.api` router (JAZ-180, commit `607264a`)

It returns this slim envelope to the React card:

```json
{
  "customerName": "Sunset Bay Resort",
  "score": 38,
  "flag": "red",
  "narrative": "12 open tickets ...",
  "actions": [
    { "title": "Schedule exec sync", "rationale": "...", "owner": "AM" }
  ],
  "streamlitUrl": "https://ops.jazzware.com/account/123"
}
```

## Local dev / preview

1. Install HubSpot CLI: `npm install -g @hubspot/cli@latest`
2. `hs init` → authenticate against the Jazzware dev portal
3. From this directory:
   ```bash
   npm install
   npm run dev      # hs project dev — hot-reload preview
   ```
4. The card renders against `sample-account.json` until `CUSTOMER_BRAIN_URL`
   is set.

## Deploy

```bash
npm run upload     # hs project upload
```

Then in the HubSpot UI: Settings → Integrations → Private apps → Customer
Brain → set secrets → install on production portal.

## Open blockers

- HubSpot developer portal access for the Jazzware tenant — required to
  actually deploy the extension. Tracked separately; the card is functional
  the moment access lands.
- Backend rate limits / auth: confirm whether the customer-brain service
  should sit behind a bearer token or HubSpot IP allowlist before wiring
  `CUSTOMER_BRAIN_URL` to a production host.

## Dependency on JAZ-180

This card depends on the `/health/score/{company_id}` endpoint shipped under
`feat/am-digest-api` (commit `607264a`). The `/account/{company_id}` endpoint
already exists in `account_intel.api.app`.
