# prometheus

Prometheus, deployed as an Argo CD `Application` pulling the
`prometheus-community/prometheus` chart with inline `helm.values`. Held back
from the `observability` app-of-apps by its `exclude` glob, so it is applied
push-based (`kubectl apply -f observability/prometheus/application.yaml`).

This file holds the rationale that, by repo convention, must **not** live as
comments inside `application.yaml` (see the root [`CLAUDE.md`](../../CLAUDE.md)).

## Storage — `persistentVolume` is on, and must stay on

8Gi on `local-path` with `retention: 30d` (**was `90d` until 2026-08-28**). It used to be
`persistentVolume.enabled: false`, which loses the **entire** history on every
pod restart. That is tolerable for cluster metrics and was not tolerable for the
Claude limits layer: `deriv(...{window="seven_day"}[6h])` needs 6h of history
just to evaluate, and testing the 72-hour-reset hypothesis needed ~11
consecutive days. `local-path` pins the pod to one node — it is single-replica
and already pinned, so that costs nothing here.

**Why 90d became 30d.** `local-path` on this node is `/srv/kubernetes/local-provisioner`
on `kube-master`'s 29 GB SD card, and this TSDB had grown to **4.5 GB — the single
largest consumer there**, on the node whose free space governs whether anything can be
pulled at all. At ~50 MB/day, 30d costs ~1.5 GB and returns ~3 GB. The requirement above
is bounded and much smaller: the longest window the Claude limits layer needs is ~11
consecutive days, so 30d keeps roughly a 3× margin. Do not cut below ~15d without
re-reading that constraint — and if more history is genuinely needed, the answer is to
move this volume off the master's card, not to raise the number again.

## Resources — the CPU limit is a burst allowance, not a steady draw

`limits.cpu: "1"` / `memory: 1Gi`, `requests.cpu: 200m` / `memory: 768Mi`.

A `150m` CPU limit throttled Prometheus during WAL replay and compaction on the
Pi, so `/-/ready` never passed → the Service had no ready endpoints → Grafana
reported the datasource unreachable. The fix is CPU burst headroom plus memory
headroom for replay. Memory is safe at this ceiling only because the `kubelet`
job's keep-list is narrowed to a few namespaces (see below) — widen that and
`768Mi` OOMs again.

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
  controller (response codes, response size, latency, TLS lifetime). Feeds the
  **MCP Fleet Health** dashboard. See below.
- **`blackbox-cascade`** — L4 probes of the VPN cascade hops. See below.
- **`blackbox-mcp`** — liveness probes for the MCP servers. See below.
- **`kubelet`** / **`kubelet-stats`** — container and volume metrics scraped
  straight off the kubelet. See below.

### `claude-usage` was removed (2026-07-19)

The `claude-usage` job went away with the whole Claude limits layer:
`/api/oauth/usage` requires the `user:profile` scope and there is no way to get
a token carrying it (`setup-token` does not grant it, and `.credentials.json` is
no longer refreshed). The exporter and its rules are preserved in the
`claude-observability` repo — restore both if a supported way to obtain that
scope ever appears. The `claude-forecast` recording-rule group and the
`claude-limits` alerting group were dropped in the same change (see
[Rules](#rules--serverfiles) below).

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

The controller's metrics Service is deliberately **not** annotated
`prometheus.io/scrape` — see
[`../../platform/nginx/application.yaml`](../../platform/nginx/application.yaml).
That is what keeps the default job from picking it up behind this one's back.

**Do not add `namespace` or `pod` target labels to this job.** The nginx
metrics already carry `namespace` and `ingress`, and those describe the
*Ingress resource* (`mcp`, `litellm`, …), not the controller. A target label of
the same name does not overwrite them — Prometheus renames the scraped one to
`exported_namespace`, which silently breaks every `{namespace="mcp"}` filter on
the dashboard.

## `blackbox-cascade` — L4 probes of the VPN cascade

`module: tcp_connect` against each hop of the VPN cascade
(home → RU relay → GCP REALITY), via the `blackbox-exporter` sidecar (see
[`../blackbox-exporter/README.md`](../blackbox-exporter/README.md)).

The targets are **real public IPs, so they are not in this repo** — this repo is
public. They live in a gitignored `blackbox-targets` Secret in ns `prometheus`,
mounted at `/etc/prometheus/blackbox-targets` and consumed as `file_sd_configs`.
The `hop` / `leg` labels come from the Secret too. Because `file_sd` is watched,
editing an IP is a re-apply of the Secret with **no Prometheus restart**:

```sh
kubectl apply -f observability/blackbox-exporter/blackbox-targets.secret.yaml
```

The mount is a plain `extraVolumes` + `extraVolumeMounts` pair rather than the
chart's `extraSecretMounts`, which renders a Deployment shape that Argo CD's
`ServerSideApply` mangles. `optional: true` keeps the pod startable on a cluster
where the Secret has not been created yet — the job then simply has no targets.

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

## `kubelet` and `kubelet-stats` — why they are hand-written too

The chart's default `kubernetes-nodes` / `kubernetes-nodes-cadvisor` jobs sit at
`up=0` on this cluster: they verify the kubelet's serving certificate through
`ca_file`, and that cert is **not** signed by the cluster CA. Both jobs here go
straight to `:10250` with `insecure_skip_verify: true` and the ServiceAccount
bearer token instead.

- **`kubelet`** — container metrics (CPU, throttling, memory, network) from the
  kubelet's cadvisor endpoint. `job_name: kubelet` **plus** an explicit
  `metrics_path=/metrics/cadvisor` target label are both required: that exact
  pair is what kube-prometheus-style dashboards (the CNPG ones) query on, so
  changing either name silently empties those panels.

  Cardinality is the reason for the two `metric_relabel_configs` keeps: a
  restricted metric-family list **and** a restricted namespace list
  (`vpn|n8n|litellm|vikunja`). Cluster-wide cadvisor already OOM-killed this
  Prometheus at `768Mi` once — do not widen the namespace regex without raising
  the memory limit first.

- **`kubelet-stats`** — the kubelet's *own* `/metrics`, kept down to
  `kubelet_volume_stats_*` for the Volume Space panels. Low cardinality (one PVC
  per pod), so no namespace filter is needed here.

## Rules — `serverFiles`

Rules are duplicated from `claude-observability/deploy/rules/claude.rules.yaml`.
The duplication is forced, not sloppy: without prometheus-operator there is no
`PrometheusRule` CRD, and this chart reads rules **only** from `serverFiles`. If
you change a rule, change it in both places.

`recording_rules.yml` carries one group, `kube-cadvisor`. Its single rule
(`node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate`)
exists purely to feed the "CPU Utilisation" panel of the kube-prometheus-style
CNPG dashboards — that recording rule normally ships with the operator, which
this Prometheus does not have.

Two groups were **removed on 2026-07-19** along with the Claude limits layer:

- `claude-forecast` (recording) — without `claude_limit_*` series it evaluates
  to nothing. Rules preserved in `claude-observability`.
- `claude-limits` (alerting) — the limits layer is blocked on the missing
  `user:profile` scope (see [Extra scrape jobs](#claude-usage-was-removed-2026-07-19)
  above), so `ClaudeUsageScrapeFailing` would have fired forever with no
  possible resolution.

What remains is the `claude-service` group, which alerts off the
`claude-status` exporter (see [`../claude-status/`](../claude-status/README.md))
and is unaffected.
