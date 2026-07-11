# grafana-mcp

MCP server for the **local** Grafana (`grafana` namespace), deployed via the
bjw-s `app-template` chart (same pattern as `basic-memory`).

## Why these values

- **Image `grafana/mcp-grafana`** — official Grafana MCP server (Go). Its Docker
  entrypoint defaults to SSE on `localhost:8000`, which binds loopback only and
  is unreachable from kube probes / the service. Hence the args
  `-t streamable-http --address 0.0.0.0:8000` — streamable-HTTP transport bound
  to all interfaces. Serves MCP at `/mcp` on port 8000.
- **`GRAFANA_URL=http://grafana.grafana.svc.cluster.local/grafana`** — in-cluster
  Grafana service (port 80 → container 3000). The `/grafana` suffix is required
  because the Grafana instance runs with `serve_from_sub_path: true`
  (root_url `/grafana`).
- **`GRAFANA_SERVICE_ACCOUNT_TOKEN`** — from secret `mcp-grafana-token`
  (out-of-band, never committed). Backed by a Grafana service account
  `mcp-grafana` (Admin role). Recreate with:
  `POST /api/serviceaccounts` + `POST /api/serviceaccounts/{id}/tokens` using
  admin creds from the `grafana` secret. Store BOM-safe
  (`printf '%s' "$token" > f; kubectl create secret --from-file=...=f`) — Git
  Bash pipes corrupt the value to UTF-16LE otherwise.
- **`nodeSelector: kube-worker-3`** — co-located with the Grafana pod (also on
  worker-3), which has the free CPU/mem headroom (the 1 GB workers do not).
- **No ingress.** Access is via **litellm**: the server is registered in
  `ai/litellm` `mcp_servers` (`http://grafana-mcp.mcp.svc.cluster.local:8000/mcp`)
  and surfaced through the litellm MCP gateway, alongside jira/confluence/gitlab/
  basic_memory. No dedicated `mcp-basic-auth` ingress.

## Consumers

- **litellm** — in-cluster, aggregated MCP gateway (primary access path).
