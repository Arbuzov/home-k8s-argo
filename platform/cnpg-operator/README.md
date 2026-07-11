# cnpg-operator

[CloudNativePG](https://cloudnative-pg.io/) operator — the controller +
CRDs that let Postgres run as a first-class `Cluster` resource (HA,
failover, scheduled base backups + WAL archiving, declarative roles/DBs)
instead of the hand-rolled single-replica `postgres:15` Deployments this
cluster runs today.

Chart `cloudnative-pg` `0.29.0` → operator **v1.30.0**, from the official
`https://cloudnative-pg.io/charts` repo. Namespace `cnpg-system`.

## What this Application deploys — and what it does *not*

This app installs **only the operator and its CRDs**. It creates **no
`Cluster`** and therefore touches none of the existing databases. Syncing
it is safe and idle: the controller just watches for `Cluster` /
`ScheduledBackup` / `Pooler` CRs, of which there are none yet. Migrating
each existing DB onto a CNPG `Cluster` is a separate, per-service change
(see [Migration plan](#migration-plan)).

## Why it's here (platform, auto-synced)

Cluster plumbing → `platform/` group, so the platform app-of-apps picks up
`platform/cnpg-operator/application.yaml` and auto-syncs it on merge (like
`arc-operator` / `oathkeeper`; it is **not** in the `platform/bootstrap.yaml`
`exclude` glob). Two edits to `platform/project.yaml` make that legal:

- `sourceRepos` += `https://cloudnative-pg.io/charts` (the chart repo).
- `destinations` += namespace `cnpg-system` (AppProject destinations are an
  explicit allow-list; without it the sync is rejected).

`clusterResourceWhitelist` is already `*/*`, so the CRDs are allowed.

The chart repo used to live at `cloudnative-pg.github.io/charts`; that host
now `301`s to `cloudnative-pg.io/charts`, so the canonical URL is used
directly. GitHub-Pages Helm repos are reachable from this network (only
git-over-HTTPS to `github.com` is DPI-filtered — Argo pulls this repo over
SSH), same as the `arc-operator` / `metrics-server` charts.

## Sync options

`ServerSideApply=true` + `ServerSideDiff=true` — the CNPG CRDs (the
`Cluster` CRD especially) are large enough to blow the 262 KiB
`last-applied-configuration` annotation limit under client-side apply.
Server-side apply sidesteps it and keeps the diff clean. Same idiom as
`arc-operator`.

## Resources & scheduling

The operator is a single stateless controller — no PVC, reschedules
freely. Footprint is small: `50m` / `96Mi` requested, `192Mi` memory
limit (no CPU limit — it idles near-zero and should be free to burst
during a failover/reconcile storm).

A **soft** `nodeAffinity` prefers the 8 GB nodes (`kube-master`,
`kube-worker-3`) over the 1 GB `kube-worker-1/2`, which saturate fast — but
it's a preference, not a pin, so the operator still schedules if both big
nodes are full (the hard-pin-leaves-it-Pending lesson from `litellm`).

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

1. **Operator only** (this change) — install, confirm `cnpg-system` healthy,
   `kubectl cnpg status` clean. No data touched.
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

## Smoke test (after sync)

```sh
kubectl -n cnpg-system get deploy,pod
kubectl get crd | grep cnpg.io          # clusters, backups, poolers, ...
kubectl cnpg status -A                   # cnpg plugin: kubectl krew install cnpg
```
