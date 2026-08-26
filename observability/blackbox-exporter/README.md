# blackbox-exporter

`prometheus-community/prometheus-blackbox-exporter`, deployed as an Argo CD
`Application` with inline `helm.values` and reconciled by the `observability`
app-of-apps. It is a de-facto **sidecar for Prometheus**: it runs in the
`prometheus` namespace and exists only to answer that Prometheus's `/probe`
requests.

This file holds the rationale that, by repo convention, must **not** live as
comments inside `application.yaml` (see the root [`CLAUDE.md`](../../CLAUDE.md)).

## `project: default`, not `observability`

Same reason as the sibling `prometheus` Application: the destination namespace
is `prometheus`, which is not in the `observability` AppProject's
`destinations`. Sharing a namespace with Prometheus is the point — it is what
lets the scrape jobs address this exporter by the short in-cluster name.

## `fullnameOverride: blackbox-exporter`

Without it the chart prefixes the release name and the Service ends up with a
longer, version-coupled name. The override pins the address the Prometheus jobs
hard-code:

```text
blackbox-exporter.prometheus.svc:9115
```

Change it and every `relabel_configs` `replacement:` in
[`../prometheus/application.yaml`](../prometheus/application.yaml) has to change
with it.

## Modules — the chart default is not enough

**The chart's default `config` ships only `http_2xx`**, so every module this
cluster actually probes with has to be declared here explicitly.

Helm deep-merges maps, so these entries are **added alongside** the chart's
`http_2xx` rather than replacing it — `http_2xx` survives and is harmless, no
target uses it. Adding a module here therefore never silently removes another.

### `tcp_connect`

L4 probe used by the `blackbox-cascade` job to check each hop of the VPN cascade
(home → RU relay → GCP REALITY). The hop addresses are real public IPs and are
**not** in this repo — they live in a gitignored `blackbox-targets` Secret that
Prometheus mounts as a `file_sd`. See
[`../prometheus/README.md`](../prometheus/README.md) and
[`../README.md`](../README.md).

### `mcp_http`

Liveness probe for the MCP fleet, used by the `blackbox-mcp` job. It is a
separate module rather than `http_2xx` because **a healthy MCP server does not
answer 2xx to `GET /`**: MCP is JSON-RPC over POST/SSE, so a live server
answers `404` / `405` / `406` / `426`, and anything behind `oathkeeper` answers
`401` / `403`. With the stock `http_2xx` module all nine targets would report red
on a completely healthy fleet — hence the wide `valid_status_codes` list.

`preferred_ip_protocol: ip4` because the targets are ClusterIPs.

**What this probe does and does not assert.** It answers "the HTTP cycle is
alive and the endpoint responds". It does **not** answer "the response is
semantically correct" — nothing checks that from the outside, and the *Blind
spots* panel on the MCP dashboard says so explicitly. Read a green probe as
"reachable", not as "working".

## Resources

`requests.cpu: 10m` / `memory: 24Mi`, `limits.memory: 48Mi`. The exporter is
idle between probes; this is sized to stay out of the way on a Pi-class node.
