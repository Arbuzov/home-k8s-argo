# prometheus

Prometheus, deployed as an Argo CD `Application` pulling the
`prometheus-community/prometheus` chart with inline `helm.values`. Reconciled by
the `observability` app-of-apps. The server keeps an 8Gi `local-path` PV with
`retention: 90d` — the `persistentVolume.enabled: false` this README used to
describe is long gone, see the rationale comment in `application.yaml`.

## Extra scrape jobs

`extraScrapeConfigs` carries jobs the chart's default kubernetes SD does not
cover:

- **`cloudnative-pg`** — CloudNativePG pod metrics (port 9187).
- **`argo-cd`** — Argo CD component metrics, feeding the Grafana Argo CD
  dashboard (see [`../grafana/README.md`](../grafana/README.md)). Argo CD here
  is installed with metrics **Services disabled** (no `*-metrics` Services, no
  ServiceMonitor), but every component container still exposes its metrics port
  named `metrics` (controller 8082, server 8083, repo-server 8084,
  applicationset 8080, notifications 9001). So the job uses `role: pod` scoped to
  the `argo-cd` namespace and keeps only container ports named `metrics` — one
  job covers all components regardless of how many, and needs no change if Argo CD
  turns its metrics Services back on. `component` is relabeled from the pod's
  `app.kubernetes.io/component` label so the dashboard can split by controller /
  server / repo-server.
- **`ingress-nginx`** — HTTP telemetry for everything behind the ingress
  controller. See below.
- **`blackbox-mcp`** — liveness probes for the MCP servers. See below.

## `ingress-nginx` — why a hand-written job and not an annotation

The obvious wiring is to annotate the controller's metrics Service with
`prometheus.io/scrape: "true"` and let the chart's default
`kubernetes-service-endpoints` job find it. That job has no
`metric_relabel_configs`, so it would ingest the *entire* nginx metric surface
on a Raspberry Pi. The keep-list here admits six families — the ones panels
actually read:

```text
nginx_ingress_controller_{requests,response_size_sum,response_size_count,
                          request_duration_seconds_{bucket,count},
                          ssl_expire_time_seconds}
```

`request_duration_seconds_bucket` is by far the most expensive (12 buckets per
`host`/`path`/`method`/`status` combination). At 16 Ingresses that is fine; it
is the first thing to drop if the TSDB starts growing.

`ssl_expire_time_seconds` is in the keep-list but **produces no series on this
cluster**, verified after deployment. TLS terminates on the Keenetic router and
the controller only ever sees plain HTTP, so it holds no certificate to report.
The entry is kept because it costs nothing when empty and starts working by
itself if TLS ever moves onto the ingress — but **certificate expiry for
`dev.whitediver.keenetic.link` is currently monitored by nothing**, and the MCP
dashboard's certificate panel was deleted rather than left permanently empty,
because an empty panel reads as "fine" when it means "not covered".

**Do not add `namespace` or `pod` target labels to this job.** The nginx
metrics already carry `namespace` and `ingress`, and those describe the
*Ingress resource* (`mcp`, `litellm`, …), not the controller. A target label of
the same name does not overwrite them — Prometheus renames the scraped one to
`exported_namespace`, which silently breaks every `{namespace="mcp"}` filter on
the dashboard.

## `blackbox-mcp` — the probe the sandbox dashboard does not have

The MCP fleet dashboard is otherwise built entirely on traffic that clients
happen to send, which cannot distinguish "this server is healthy" from "nobody
called this server today". These probes make that distinction.

Targets are the **ClusterIP Services directly**, not the public hostname.
Hairpin NAT back through the Keenetic router from inside the cluster is
unreliable, so probing `https://dev.whitediver.keenetic.link/mcp/...` would
measure the router rather than the server. Probing Services also covers `codex`,
which has no Ingress at all.

The trade-off is explicit: **this probe does not test the
router → ingress → oathkeeper path.** That path is covered by `ingress-nginx`
above, and the two are complementary — neither alone is sufficient.

`openconnect-gateway` is deliberately absent: it is a headless Service and a
VPN gateway, not an HTTP endpoint, so an HTTP probe would be permanently red.

The `mcp_http` blackbox module (defined in `../blackbox-exporter`) accepts a
wide list of status codes rather than 2xx only. MCP is JSON-RPC over POST/SSE:
a healthy server answers `GET /` with 404/405/406/426, and anything behind
oathkeeper answers 401/403. With the stock `http_2xx` module all nine targets
would report red on a completely healthy fleet.
