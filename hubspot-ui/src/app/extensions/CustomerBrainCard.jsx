// Customer Brain card — HubSpot company sidebar (JAZ-110).
//
// Reads context.crm.objectId, calls the serverless `fetchAccount` function
// which proxies to the customer-brain service `GET /account/{company_id}`
// (and `/health/score/{company_id}` for the score envelope). Renders the
// risk flag, narrative, and top-3 next-best-actions. "See full view" opens
// the internal Streamlit dashboard.
import React, { useEffect, useState } from "react";
import {
  hubspot,
  Flex,
  Tile,
  Text,
  Heading,
  Tag,
  Button,
  LoadingSpinner,
  EmptyState,
  Divider,
  Link,
} from "@hubspot/ui-extensions";

const FLAG_VARIANT = {
  red: "danger",
  yellow: "warning",
  green: "success",
};

const FLAG_LABEL = {
  red: "🔴 RED",
  yellow: "🟡 YELLOW",
  green: "🟢 GREEN",
};

hubspot.extend(({ context, runServerlessFunction }) => (
  <CustomerBrainCard
    context={context}
    runServerless={runServerlessFunction}
  />
));

function CustomerBrainCard({ context, runServerless }) {
  const companyId = context?.crm?.objectId;
  const [state, setState] = useState({ loading: true, error: null, data: null });

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!companyId) {
        setState({ loading: false, error: "No company in context", data: null });
        return;
      }
      try {
        const resp = await runServerless({
          name: "fetchAccount",
          parameters: { companyId: String(companyId) },
        });
        if (cancelled) return;
        if (resp?.status === "SUCCESS" && resp.response) {
          setState({ loading: false, error: null, data: resp.response });
        } else {
          setState({
            loading: false,
            error: resp?.response?.error || "Unable to load Customer Brain data",
            data: null,
          });
        }
      } catch (e) {
        if (!cancelled) {
          setState({ loading: false, error: String(e?.message || e), data: null });
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [companyId, runServerless]);

  if (state.loading) {
    return (
      <Flex direction="column" gap="md" align="center">
        <LoadingSpinner label="Loading Customer Brain…" />
      </Flex>
    );
  }
  if (state.error || !state.data) {
    return (
      <EmptyState
        title="Customer Brain unavailable"
        layout="vertical"
        reverseOrder
      >
        <Text format={{ fontStyle: "italic" }}>{state.error || "No data"}</Text>
      </EmptyState>
    );
  }

  const { score, flag, narrative, actions, streamlitUrl, customerName } = state.data;
  const flagVariant = FLAG_VARIANT[flag] || "default";

  return (
    <Flex direction="column" gap="md">
      <Flex direction="row" justify="between" align="center">
        <Heading>{customerName || "Unknown account"}</Heading>
        <Tag variant={flagVariant}>{FLAG_LABEL[flag] || flag}</Tag>
      </Flex>

      <Tile compact>
        <Flex direction="row" justify="between" align="center">
          <Text format={{ fontWeight: "bold" }}>Risk score</Text>
          <Heading>{score}/100</Heading>
        </Flex>
      </Tile>

      {narrative ? (
        <Tile>
          <Text>{narrative}</Text>
        </Tile>
      ) : null}

      {Array.isArray(actions) && actions.length > 0 ? (
        <>
          <Divider />
          <Heading>Next best actions</Heading>
          {actions.slice(0, 3).map((a, i) => (
            <Tile key={a.id || i} compact>
              <Flex direction="column" gap="xs">
                <Text format={{ fontWeight: "bold" }}>
                  {i + 1}. {a.title}
                </Text>
                {a.rationale ? <Text>{a.rationale}</Text> : null}
                {a.owner ? (
                  <Text format={{ fontStyle: "italic" }}>Owner: {a.owner}</Text>
                ) : null}
              </Flex>
            </Tile>
          ))}
        </>
      ) : null}

      {streamlitUrl ? (
        <Flex direction="row" justify="end">
          <Link href={streamlitUrl} external>
            <Button>See full view →</Button>
          </Link>
        </Flex>
      ) : null}
    </Flex>
  );
}
