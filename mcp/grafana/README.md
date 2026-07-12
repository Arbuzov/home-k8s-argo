# grafana-mcp

MCP server for the **local** Grafana (`grafana` namespace), deployed via the
bjw-s `app-template` chart (same pattern as `basic-memory`).

## Why these values

- **Image `grafana/mcp-grafana`** — official Grafana MCP server (Go). Its Docker
  entrypoint defaults to SSE on `localhost:8000`, which binds loopback only and
  is unreachable from kube probes / the service. Hence the args
  `-t streamable-http --address 0.0.0.0:8000` — streamable-HTTP transport bound
  to all interfaces. Serves MCP at `/mcp` on port 8000.
- **`--allowed-hosts '*'`** — mcp-grafana validates the `Host` header for
  DNS-rebinding protection and by default only allows loopback of `--address`,
  which rejects the ClusterIP service DNS name litellm connects with
  (`403 forbidden: host not allowed`). `*` disables the check; safe here because
  the server is internal-only (no ingress, cluster network, reached solely via
  litellm). The `tcpSocket` probes are unaffected (no Host header).
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
- **Public path via the shared gateway.** The server is registered in
  `ai/litellm` `mcp_servers` (`http://grafana-mcp.mcp.svc.cluster.local:8000/mcp`)
  and surfaced at `dev.whitediver.keenetic.link/mcp/grafana` the same way as
  jira/confluence/kubernetes: the shared [`mcp-gateway`](../mcp-gateway/gateway-ingress.yaml)
  ingress (basic-auth `mcp-basic-auth`) points `/mcp/grafana` at
  `oathkeeper-proxy:4455`, which injects the litellm key and upstreams to
  litellm `:4000` `/mcp/grafana`. Requires a matching `mcp-grafana` rule in the
  out-of-band `oathkeeper-rules` Secret (see
  [`../../platform/oathkeeper/README.md`](../../platform/oathkeeper/README.md)).
  Public name == litellm alias == `grafana`, so no path rewrite is needed.

## Consumers

- **litellm** — in-cluster, aggregated MCP gateway (also the public path via
  oathkeeper).
- **Claude connector** — `dev.whitediver.keenetic.link/mcp/grafana` (basic-auth).
