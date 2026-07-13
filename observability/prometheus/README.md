# prometheus

Prometheus, deployed as an Argo CD `Application` pulling the
`prometheus-community/prometheus` chart with inline `helm.values`. Reconciled by
the `observability` app-of-apps. Server runs non-persistent (in-memory TSDB,
`persistentVolume.enabled: false`) — metrics are ephemeral across restarts.

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
