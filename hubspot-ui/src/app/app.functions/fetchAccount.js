// Serverless proxy for the Customer Brain card.
//
// Inputs (via parameters):
//   companyId — HubSpot company object id (string)
//
// Calls the customer-brain service:
//   GET {CUSTOMER_BRAIN_URL}/account/{companyId}        → account detail
//   GET {CUSTOMER_BRAIN_URL}/health/score/{companyId}   → score envelope
//
// Returns a slim envelope the React card knows how to render. Falls back to
// a sample fixture when CUSTOMER_BRAIN_URL is not set so devs can preview the
// card in HubSpot's "developer mode" without standing up the backend.

const SAMPLE = require("./sample-account.json");

exports.main = async (context = {}) => {
  const { parameters = {} } = context;
  const companyId = String(parameters.companyId || "").trim();
  if (!companyId) {
    return { error: "companyId is required" };
  }

  const base = process.env.CUSTOMER_BRAIN_URL;
  const apiKey = process.env.CUSTOMER_BRAIN_API_KEY;
  const streamlitBase = process.env.STREAMLIT_BASE_URL || "";

  if (!base) {
    // Dev mode: serve fixture so the card renders end-to-end.
    return {
      ...SAMPLE,
      streamlitUrl: streamlitBase ? `${streamlitBase}/account/${companyId}` : null,
    };
  }

  const headers = { Accept: "application/json" };
  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;

  const fetchJson = async (path) => {
    const r = await fetch(`${base.replace(/\/$/, "")}${path}`, { headers });
    if (!r.ok) {
      const body = await r.text().catch(() => "");
      throw new Error(`${path} → ${r.status} ${body.slice(0, 200)}`);
    }
    return r.json();
  };

  try {
    const [account, scoreEnvelope] = await Promise.all([
      fetchJson(`/account/${encodeURIComponent(companyId)}`),
      fetchJson(`/health/score/${encodeURIComponent(companyId)}?use_claude=false`).catch(
        () => null,
      ),
    ]);

    const score = scoreEnvelope?.score ?? account?.risk_score ?? null;
    const flag = scoreEnvelope?.flag ?? (account?.risk_flag || "green");
    const narrative =
      scoreEnvelope?.narrative ||
      account?.narrative ||
      account?.summary ||
      "";
    const actions =
      account?.next_best_actions ||
      account?.actions ||
      scoreEnvelope?.next_best_actions ||
      [];

    return {
      customerName: account?.name || account?.customer_name || `Company ${companyId}`,
      score,
      flag,
      narrative,
      actions,
      streamlitUrl: streamlitBase ? `${streamlitBase}/account/${companyId}` : null,
    };
  } catch (e) {
    return { error: e.message || String(e) };
  }
};
