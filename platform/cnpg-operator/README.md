# cnpg-operator

[CloudNativePG](https://cloudnative-pg.io/) operator — the controller +
CRDs that let Postgres run as a first-class `Cluster` resource (HA,
failover, scheduled base backups + WAL archiving, declarative roles/DBs)
instead of the hand-rolled single-replica `postgres:15` Deployments this
cluster runs today.

Chart `cloudnative-pg` `0.29.0` → operator **v1.30.0**, from the official
`https://cloudnative-pg.github.io/charts` repo. Namespace `cnpg-system`.

## What this Application deploys — and what it does *not*

This app installs **only the operator** (controller Deployment, RBAC,
webhooks, ServiceAccount). It creates **no `Cluster`**, so it touches none
of the existing databases — syncing it is idle and safe. The CRDs are
**not** chart-managed here; they are applied out-of-band (see below).
Migrating each existing DB onto a CNPG `Cluster` is a separate, per-service
change (see [Migration plan](#migration-plan)).

## CRDs are applied out-of-band

This cluster's Argo CD **globally excludes** `CustomResourceDefinition` from
its watch set (`configs.cm.resource.exclusions`, `clusters: ['*']` — see
[`../argo-cd/README.md`](../argo-cd/README.md) → *CRDs are NOT managed by
Argo*). Argo therefore will **not** apply a chart's CRDs during a sync — the
same constraint that makes `argo-cd` set `crds.install: false` and
`arc-operator` set `skipCrds: true`.

So this app sets `crds.create: false`: the chart renders no CRDs (which would
otherwise sit in the desired state as permanent `ExcludedResourceWarning`s),
and the CNPG CRDs are installed **by hand once, before the first sync**, and
re-applied on each chart bump. Run it from a workstation with normal egress:

```sh
helm repo add cnpg https://cloudnative-pg.github.io/charts
helm template cnpg cnpg/cloudnative-pg --version 0.29.0 \
  -n cnpg-system --include-crds \
  | yq 'select(.kind == "CustomResourceDefinition")' \
  | kubectl apply --server-side --force-conflicts -f -
```

Server-side apply avoids the last-applied-config size limit on the large
`clusters.postgresql.cnpg.io` CRD. **Order matters:** CRDs first, then let
Argo sync the operator — otherwise the controller starts with nothing to
watch. CNPG CRD deltas across minor versions are additive, so re-applying on
an upgrade is forward/backward compatible.

## Monitoring

`monitoring.podMonitorEnabled: false` — deliberately. This cluster runs the
plain `prometheus-community/prometheus` chart (**no Prometheus Operator**), so
the `PodMonitor` CRD (`monitoring.coreos.com/v1`) does not exist; flipping this
to `true` would emit a `PodMonitor` whose kind can't be recognized and **fail
the sync**.

On this stack Prometheus discovers targets by `prometheus.io/scrape` pod
annotations, not PodMonitors. The operator by itself exposes little worth
alerting on — its Deployment availability already shows via kube-state-metrics,
and the app's own health shows in Argo. The metrics that matter are per-instance
Postgres stats, and those arrive at the Cluster-migration stage: a CNPG
`Cluster` exposes metrics on `:9187`, scraped by annotating its instance pods
(`.spec.monitoring` in the `Cluster` CR + the standard `prometheus.io/*`
annotations). Wire that in with the first `Cluster`, not here.

## Why it's here (platform, auto-synced)

Cluster plumbing → `platform/` group, so the platform app-of-apps picks up
`platform/cnpg-operator/application.yaml` and auto-syncs it on merge (like
`arc-operator` / `oathkeeper`; it is **not** in the `platform/bootstrap.yaml`
`exclude` glob). Two edits to `platform/project.yaml` make that legal:

- `sourceRepos` += `https://cloudnative-pg.github.io/charts` (the chart repo,
  matching the repo's other `*.github.io` Helm sources).
- `destinations` += namespace `cnpg-system` (AppProject destinations are an
  explicit allow-list; without it the sync is rejected).

`clusterResourceWhitelist` is already `*/*`.

The chart tarballs (unlike the index) are GitHub **Releases** assets on
`github.com`, the host this network DPI-filters (why Argo pulls this repo over
SSH). Whether Argo's repo-server can fetch them over plain HTTPS is unverified —
if the first sync fails on the chart pull, vendor CNPG's all-in-one
`cnpg-1.30.0.yaml` into the repo and switch this to a path-based Application
served over SSH.

## Sync options

`ServerSideApply=true` + `ServerSideDiff=true` — keeps the operator's own
resources (RBAC, webhook configs) on server-side apply and gives clean diffs.
Same idiom as `arc-operator`. (CRDs are handled out-of-band, above.)

## Resources & scheduling

The operator is a single stateless controller — no PVC, reschedules
freely. Footprint is small: `50m` / `96Mi` requested, `192Mi` memory
limit (no CPU limit — it idles near-zero and should be free to burst
during a failover/reconcile storm; a CPU limit once crashlooped
`csi-smb-node` here, so we don't add one lightly).

A **soft** `nodeAffinity` prefers the 8 GB nodes (`kube-master`,
`kube-worker-3`) over the 1 GB `kube-worker-1/2`, which saturate fast — but
it's a preference, not a pin, so the operator still schedules if both big
nodes are full (the hard-pin-leaves-it-Pending lesson from `litellm`). It
matches the repo's convention of pinning by `kubernetes.io/hostname` (the
`argo-cd` controller uses the same two nodes); there is no node-tier label to
target instead, and nodes aren't managed in this repo.

## No out-of-band secrets

The operator itself needs none. Per-`Cluster` credentials (the app user
password, and any restore/backup object-store keys) come later, with each
DB's own `Cluster` manifest.

## Existing Postgres inventory (what this will consolidate)

As of this change the cluster runs **three** hand-rolled Postgres
instances, all plain `postgres:15`, single-replica, no operator, no HA,
per-app secrets, and only one with a real logical backup:

| DB | Namespace | Form | Storage | Node | Backup |
| --- | --- | --- | --- | --- | --- |
| `litellm-postgres` | `litellm` | Deployment | `hostPath` `/var/lib/litellm-postgres-data` | pinned `kube-worker-3` | none |
| `postgres-n8n` | `n8n` | Deployment (inline `extraManifests`) | `hostPath` `/var/lib/postgres-n8n-data` | pinned `kube-master` | daily `pg_dump` → SMB PVC |
| `heimdall-postgres` | `heimdall` | sidecar container in the heimdall pod | PVC on `smb-pg` (5 Gi) | pinned `kube-worker-1` | none |

Common weaknesses CNPG would fix: node-pinned `hostPath` = single point of
failure (lost if the node dies / pod reschedules), no automated
backup/PITR on two of three, no failover, manual PGDATA init dance.
`hostPath`/`smb-pg` were deliberate to dodge the **Bitnami-Postgres-on-CIFS
corruption trap** — CNPG keeps that safety (it uses a real PVC per instance;
back it with `local-path`/node-local, **never** the `smb-*` StorageClasses).

## Migration plan

Per-DB, one at a time, lowest-risk first. Each is its own commit/PR — this
Application does not do any of it:

1. **Operator only** (this change) — apply CRDs out-of-band, sync the app,
   confirm `cnpg-system` healthy, `kubectl cnpg status` clean. No data touched.
2. **`heimdall`** first (least critical, already on a PVC) — a 1-instance
   `Cluster`, `initdb` bootstrap or `pg_dump`/restore, cut the heimdall
   container's `DB_HOST` over, drop the sidecar.
3. **`litellm`** — 1-instance `Cluster` on `local-path`; `pg_dump` the
   `hostPath` DB in, repoint `db.useExisting` at the CNPG service, retire
   `ai/litellm/db/postgres.yaml`.
4. **`n8n`** last (most moving parts: existing `pg_dump` cron, redis, backup
   PVC) — `Cluster` + `ScheduledBackup`, migrate the pg_dump/restore, then
   remove the inline `postgres-n8n` manifests.

Decide before step 2 whether these become **one shared** CNPG `Cluster`
(several DBs/roles in it) or **one `Cluster` per app** (stronger isolation,
more overhead). Recommendation for this home lab: **one shared,
single-instance `Cluster`** — the asymmetric nodes (two are 1 GB) can't host
a 2-instance HA pair usefully, and per-app clusters trebles the overhead for
three tiny DBs. Revisit HA only once two 8 GB nodes can each take an instance.

## Smoke test (after CRDs applied + sync)

```sh
kubectl get crd | grep cnpg.io          # clusters, backups, poolers, ... (from the out-of-band apply)
kubectl -n cnpg-system get deploy,pod
kubectl cnpg status -A                   # cnpg plugin: kubectl krew install cnpg
```
