# cloud-billing

Cloud resource + spend monitoring for the two external clouds this homelab
depends on: **AWS** (`us-east-2`, the VLESS/REALITY VPN EC2) and **GCP** (the
planned migration target). Metrics land in the sibling `prometheus` app and are
graphed in `grafana`.

This file holds the rationale that, by repo convention, must **not** live as
comments inside the manifests (see the root `CLAUDE.md`).

## Two layers, deliberately separated

| Layer         | AWS                                | GCP                                                |
| ------------- | ---------------------------------- | -------------------------------------------------- |
| **Resources** | `yace` — CloudWatch `AWS/EC2`      | `stackdriver-exporter` — Cloud Monitoring          |
| **Money**     | `yace` — CloudWatch `AWS/Billing`  | not yet — see [GCP spend](#gcp-spend--not-here-yet) |

Resource metrics refresh on a 5-minute cadence; spend refreshes every ~6 hours
and is billed per API call. Mixing them into one scrape loop is what makes
cloud-cost dashboards expensive, so they are separate jobs inside one exporter
with very different `period` / `length`.

**The metric that actually matters here is egress, not CPU.** Both VMs are
free-tier-class; the only line item that can realistically run up a bill is
network out (AWS: 100 GB/mo free, then ~$0.09/GB; GCP Standard tier: 200 GiB/mo
free, then ~$0.085/GiB). `NetworkOut` is the panel to watch on a VPN box.

## Why `AWS/Billing`, not Cost Explorer

The Cost Explorer API is the "proper" spend source, and it is a trap at this
scale: **$0.01 per request, and every pagination page counts as its own
request.** A dashboard refreshing every 30s, or an exporter polling hourly,
would cost more than the ~$5-10/mo account it is monitoring.

CloudWatch's `AWS/Billing` / `EstimatedCharges` metric is free, carries a
`ServiceName` dimension for the per-service breakdown, and refreshes about every
6 hours — which is as fresh as Cost Explorer gets anyway (it has a ~24h lag on
current-month data). Good enough here; revisit only if per-tag or per-resource
attribution is ever needed.

Two hard requirements, both easy to miss:

1. **`AWS/Billing` only exists in `us-east-1`**, regardless of where the
   resources run. That is why the `billing` job pins `us-east-1` while the EC2
   discovery job uses `us-east-2`. This is not a copy-paste error — don't
   "fix" it.
2. **Billing metrics are off by default.** Enable *Receive Billing Alerts* in
   Billing → Billing preferences (root account, `us-east-1`), or the namespace
   stays empty and the panels read **No data** forever with no error anywhere.

`billing` is a `customNamespace` job rather than `static`: a `static` job
requires every dimension to be spelled out, which would mean one job per AWS
service. `customNamespace` runs `ListMetrics` and returns all dimension
combinations, so the per-`ServiceName` series appear on their own.

## Why `prometheus.io/scrape-slow`, not a ServiceMonitor

**This cluster runs plain Prometheus (chart `prometheus`), not the Operator** —
there are no `ServiceMonitor` CRDs, and Argo CD excludes CRDs cluster-wide
anyway. Both charts therefore leave `serviceMonitor.enabled` at its default
`false`.

Scraping is wired through pod annotations instead. The stock chart ships a
`kubernetes-pods-slow` job with `scrape_interval: 5m` / `scrape_timeout: 30s`,
keyed off `prometheus.io/scrape-slow: "true"` — exactly the cadence CloudWatch
wants, and free to use. Plain `prometheus.io/scrape` would scrape at the 1m
global interval: 5x the CloudWatch `GetMetricData` calls for data that only
changes every 5 minutes (and every 6 hours for billing).

The upside of annotations over `extraScrapeConfigs` is that
`observability/prometheus/application.yaml` is **push-based** (held back by the
`bootstrap.yaml` exclude glob) — adding a scrape job there would need a manual
`kubectl apply` to take effect. Annotations are picked up by the running
Prometheus with no change to that app at all.

## Why `listen-address` is set explicitly

`extraArgs.listen-address: 0.0.0.0:5000` is **load-bearing**. YACE defaults to
`127.0.0.1:5000` and the chart's `command:` does not pass the flag — so without
it the exporter binds to loopback only, the chart's own `httpGet` liveness /
readiness probes fail against the pod IP, and the pod never goes ready. Don't
remove it.

## Secrets (out-of-band)

Per the repo's secrets model, credentials are **not** in git.

### `yace-aws-credentials` (ns `cloud-billing`)

The chart expects the keys `access_key` and `secret_key` (not the AWS-SDK
spelling). The cluster is not in AWS, so there is no IRSA / instance role to
assume — a static IAM user is the only option. Give it read-only access.

Fill in `yace-aws-credentials.secret.yaml` in the repo root (gitignored through
the `*.secret.yaml` glob) and apply it. That file also carries the
`cloud-billing` Namespace, so it works before Argo CD has ever synced this group
— which is the point, since the Secret has to exist *first*:

```sh
kubectl --context kubernetes-local apply -f yace-aws-credentials.secret.yaml
```

IAM policy for that user (nothing beyond read):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "tag:GetResources",
        "cloudwatch:ListMetrics",
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics"
      ],
      "Resource": "*"
    }
  ]
}
```

Note there is **no `ce:*`** in that policy, on purpose — it makes the expensive
Cost Explorer API unreachable even by accident.

The EC2 discovery job finds instances through the Resource Groups Tagging API,
which **only returns resources that carry at least one tag**. An untagged VPN
instance is invisible to `yace` — give it a `Name` tag (the config exports that
tag onto the metrics).

### `stackdriver-exporter-credentials` (ns `cloud-billing`)

A GCP service account JSON key with role `roles/monitoring.viewer`, stored under
the key `credentials.json`. The chart maps that key onto the fixed in-pod path
`/etc/secrets/service-account/credentials.json` and points
`GOOGLE_APPLICATION_CREDENTIALS` at it, so the key name here only has to match
`stackdriver.serviceAccountSecretKey` in the Application.

Same pattern — `stackdriver-exporter-credentials.secret.yaml` in the repo root:

```sh
kubectl --context kubernetes-local apply -f stackdriver-exporter-credentials.secret.yaml
```

Or, to skip pasting JSON into a block scalar, straight from the downloaded key:

```sh
kubectl --context kubernetes-local -n cloud-billing create secret generic stackdriver-exporter-credentials \
  --from-file=credentials.json=./sa-key.json
```

## `stackdriver-exporter` is held back

It sits behind the `observability/bootstrap.yaml` exclude glob and is
**push-based** until the GCP project exists:

```sh
kubectl apply -f observability/cloud-billing/application-stackdriver-exporter.yaml
```

Two things must be filled in first:

- `stackdriver.projectIds` is the literal placeholder `CHANGEME-gcp-project-id`.
  A project ID is not a secret and belongs in git — it is a placeholder only
  because the project doesn't exist yet.
- The `stackdriver-exporter-credentials` Secret must exist, or the pod sits in
  `CreateContainerConfigError`.

`stackdriver.metrics.typePrefixes` is explicitly `""` and `projectId` is `""`:
both are the chart's deprecated single-value options, and leaving them at their
defaults (`compute.googleapis.com/instance/cpu` and the string `FALSE`) renders a
second, conflicting set of CLI flags alongside the plural `prefixes` /
`projectIds` ones.

## GCP spend — not here yet

GCP has **no equivalent of `EstimatedCharges`** — Cloud Monitoring exposes no
cost metric at all. The only source is Cloud Billing → BigQuery export: enable
the export, wait up to 24h for the first rows, then query it on a schedule.

Deliberately not built yet, for two reasons:

1. **There is no GCP project or billing export to query.** Writing the job now
   means committing code that cannot be run or verified.
2. **The obvious image doesn't work here.** `google/cloud-sdk:slim` is
   **amd64-only** — every node in this cluster is arm64, so a `bq query` CronJob
   would `CrashLoopBackOff` on an exec-format error. The plan is a
   `python:3.13-slim` CronJob using the stdlib plus `openssl` to sign the
   service-account JWT and hit the BigQuery REST API directly, avoiding both the
   amd64 image and a `pip install` at runtime (PyPI over this DPI-filtered line
   is not something to depend on).

The delivery path is already in place: the `prometheus` chart runs a Pushgateway
(`prometheus-prometheus-pushgateway.prometheus.svc.cluster.local:9091`) and its
Service carries `prometheus.io/probe: pushgateway`, which the
`prometheus-pushgateway` scrape job keys on — so a CronJob can push
`gcp_billing_cost_usd` gauges and they land in Prometheus with no further wiring.

## No dashboard in this change

Panels are deliberately not committed yet. YACE's metric names depend on the
dimensions CloudWatch actually returns for this account (and on whether
`-labels-snake-case` is in play); building panels before the first successful
scrape means guessing label names and shipping a dashboard that reads **No
data**. Verify first:

```sh
kubectl --context kubernetes-local -n prometheus port-forward svc/prometheus-server 9090:80
# then query: {__name__=~"aws_billing.*"} and {__name__=~"aws_ec2.*"}
```

Then add the dashboard against the real series. Note the sibling `grafana` app
provisions dashboards by `gnetId`; a multi-cloud cost dashboard has no upstream
equivalent, so this one needs inline JSON (the `grafana` README already flags
that as the escape hatch).

## Retention caveat

`observability/prometheus` runs with `persistentVolume.enabled: false` — its TSDB
is an `emptyDir`, so **every pod restart wipes local history** and the billing
panels will show a sawtooth across restarts. Not worth fixing here: CloudWatch
retains `EstimatedCharges` for 15 months on its own, so the authoritative history
is upstream and Prometheus is only the recent window.
