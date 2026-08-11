# influxdb

InfluxDB 1.8 (`arm64v8/influxdb`) on `kube-master`, exposed at
`192.168.99.44:8086`. Primary writer is Home Assistant. Single replica,
`local-path` persistence pinned to `kube-master` via `nodeAffinityPreset`
+ `nodeSelector`.

This file holds the rationale that, by repo convention, must **not** live
as comments inside `application.yaml` (see the root `CLAUDE.md`).

## Why the config is tuned the way it is

This node is a shared 8 GB Pi-class host. The Home Assistant DB's series
cardinality is high enough that the stock config OOM-killed the pod in a
loop (observed: 1849 restarts). The settings below are the hard-won fix —
**don't revert them without understanding the failure mode.**

### `resources.limits` — `memory: 2Gi`, `cpu: 500m`

2Gi is the sustainable ceiling on this node. A *full* compaction of a
shard builds an in-memory series index that can exceed even 3.5Gi → OOM;
that's deferred (below) rather than fed more RAM. A real long-term fix
needs reduced HA cardinality or a bigger node.

### `config.data.index-version: tsi1`

On-disk TSI index instead of the default in-memory index. The HA DB's
cardinality grew until the in-memory index no longer fit in 2Gi, OOM-
killing the pod in a loop. `tsi1` keeps the series index on disk,
slashing startup and steady-state RAM. Existing shards were converted
offline with `influx_inspect buildtsi` before this was enabled; new
shards inherit `tsi1` from this setting.

### cache + snapshot tuning

`cache-max-memory-size: 512m`, `cache-snapshot-memory-size: 64m`,
`cache-snapshot-write-cold-duration: 10m`.

The earlier aggressive values (`5m` / `10s`) snapshotted a tiny TSM file
every ~10s of idle which — across the OOM-restart loop — produced ~7600
tiny TSM files whose in-memory block indexes blew past the limit. These
saner values let the cache fill before snapshotting, and the level
compactor consolidates the existing backlog into a handful of large
files.

### `compact-full-write-cold-duration: 8760h`

Full compaction of this high-cardinality DB OOMs the pod (it builds the
whole shard's series index in memory). Deferred ~1 year into the future
so `influxd` stays up; level compaction still consolidates new writes.
Drop this once HA cardinality is reduced or RAM is added.

### `livenessProbe.initialDelaySeconds: 600` + `startupProbe`

Slow WAL replay on this node was killing the pod inside the default 60s
liveness window, producing a crash loop. The long initial delay plus a
generous startup probe (120 × 10s) give WAL replay time to finish before
the kubelet starts health-checking.

## Data loss, 2026-07-18 — every database was gone

Discovered 2026-08-11: `SHOW DATABASES` returned an empty list and
`/var/lib/influxdb/data` was an empty directory (mtime 2026-07-18 09:26), 16 KB
total on an 8 Gi PVC. The PVC itself was never recreated — it is 274 days old —
so the volume survived and its contents did not. Cause not established;
`local-path` on `kube-master` plus the OOM-restart loop described above are the
obvious suspects.

All three databases were lost with it, and every writer had been getting
`404 database not found` ever since — silently, because nothing alerts on it:

| database | writer | visible symptom |
| --- | --- | --- |
| `homeassistant` | Home Assistant | none — HA logs the write failure and moves on |
| `keenetic` | [`../keenetic-grafana-monitoring/`](../keenetic-grafana-monitoring/) | pod `CrashLoopBackOff` (482 restarts) |
| `homepage` | homepage widgets | empty panels |

The ~3 weeks of history in them is unrecoverable. Retention policies are back
to the InfluxDB defaults (infinite) — the same as before the loss.

## The databases are declared — [`databases/`](databases/)

The chart creates no databases, and nothing else did either: they had been
created by hand, so the wipe above took them with it and stayed unnoticed for
three weeks. They are now reconciled by a daily `CronJob`
([`databases/cronjob.yaml`](databases/cronjob.yaml)) running `CREATE DATABASE`
for each of the three, synced by its own Application
([`application-databases.yaml`](application-databases.yaml)) — this directory
is *not* part of the excluded-from-bootstrap set that
`application.yaml` is (see below).

`CREATE DATABASE` is a no-op against an existing database, so re-running it
costs nothing and needs no `IF NOT EXISTS` guard. A `CronJob` rather than a
sync-time hook on purpose: the failure mode is the data vanishing at runtime,
which no sync would be triggered by — the reconcile has to be on a timer. A
plain `Job` would have been worse still; carrying a `ttlSecondsAfterFinished`
into an Argo-managed resource is what put `litellm` into a permanent recreate
loop (see [`../../ai/litellm/README.md`](../../ai/litellm/README.md)).

The recovery window is now ≤ 24 h instead of unbounded, but nothing yet
*alerts* on an InfluxDB write failing — a crash-looping keenetic exporter
remains the only loud symptom, and Home Assistant's writes still fail silently.

## This `application.yaml` is NOT synced — edits here do not reach the cluster

[`../bootstrap.yaml`](../bootstrap.yaml) lists `influxdb/application.yaml` in
its `directory.exclude`, so the observability app-of-apps never applies this
file. The live `Application` in `argo-cd` is whatever was last applied by hand,
and it has drifted (verified 2026-08-11):

| | git `application.yaml` | live `Application` / StatefulSet |
| --- | --- | --- |
| `resources.limits.memory` | `768Mi` | `2Gi` |
| `resources.limits.cpu` | `300m` | `1` |
| rationale comments | removed by `c57b30c` | still present |

The trim to `768Mi`/`300m` (`46da2a0`) therefore never took effect — which is
lucky, because it contradicts the OOM analysis at the top of this file, whose
whole point is that `2Gi` is the sustainable ceiling. **Do not "fix" the drift
by syncing this file as it stands**: that would drop the live limit to `768Mi`
and reopen the crash loop. Decide the number first, put it in git, then apply.

## `ignoreDifferences` — StatefulSet `volumeClaimTemplates`

The chart renders `volumeClaimTemplates` with `annotations: null`; the
API server stores them as absent, so Argo CD reports a permanent diff.
VCTs are immutable on a StatefulSet anyway, so there's nothing Argo CD
could reconcile — the diff is ignored.
