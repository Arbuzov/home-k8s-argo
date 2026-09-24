# influxdb

InfluxDB 1.8 (`arm64v8/influxdb`) on `kube-worker-3`, exposed at
`192.168.99.44:8086`. Primary writer is Home Assistant. Single replica, data on
the pre-created PVC `influxdb-data-ssd` (`local-ssd`, worker-3's USB SSD); the
PV's node affinity is the only thing that places the pod — there is no
`nodeSelector`.

`192.168.99.44` is kube-master's own address, carried as a Service
`externalIP`: kube-proxy (iptables, `externalTrafficPolicy: Cluster`) on
kube-master DNATs it to the pod wherever it runs, the same way
`localstack-external` is served. Writers outside the cluster therefore still
depend on kube-master being up; `192.168.99.93:30332` (the NodePort) is a
fallback that needs no change.

This file holds the rationale that, by repo convention, must **not** live
as comments inside `application.yaml` (see the root `CLAUDE.md`).

## Why the config is tuned the way it is

This node is a shared 8 GB Pi-class host. The Home Assistant DB's series
cardinality is high enough that the stock config OOM-killed the pod in a
loop (observed: 1849 restarts). The settings below are the hard-won fix —
**don't revert them without understanding the failure mode.**

### `resources.limits` — `memory: 2Gi`, `cpu: 1`

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

## Disk failure, 2026-09-24 — moved off kube-master

`influxdb-0` crash-looped (exit 2, `fatal error: fault` / `SIGBUS` in
`tsi1.(*LogEntry).UnmarshalBinary`, plus `input/output error` on shard files).
The cause was not InfluxDB: `/srv/kubernetes` on kube-master is a symlink to
`/mnt/usb/kubernetes`, the exFAT partition of a 2.5" USB HDD (ST1000LM024,
~44 700 power-on hours, 1.17 M load cycles, 25 pending sectors, kernel
`critical medium error`). Every `local-path` volume on kube-master lives there —
not on the SD card, as had been assumed. It is also the likeliest explanation for
the 2026-07-18 wipe above.

Recovery:

1. `sts/influxdb` scaled to 0 — each crash-loop start re-read the bad sectors and
   reset the USB bus under every other volume on that disk.
2. `rsync -a` of the old PV to `/var/tmp/influxdb-rescue-2026-09-24` on
   kube-master's SD root (kept as the rollback copy). rsync drops a file whose
   read fails rather than writing zeros, so its error lines are the loss list:
   `homeassistant/_series/00/0000`, the tsi1 index of HA shard 64 and keenetic
   shard 65 (all rebuilt, see 4), the whole `wal/keenetic/autogen/65`, and
   `wal/homeassistant/autogen/64/_00003.wal`, whose readable first ~6 MB were
   salvaged with `dd conv=noerror,sync` — the WAL loader truncates a segment at
   its first corrupt entry, so the zero-filled tail costs nothing more.
3. Copied into the new PVC `influxdb-data-ssd` on `local-ssd` (kube-worker-3).
4. Offline, before influxd ever saw the copy: every `_series` and shard `index/`
   removed and rebuilt with `influx_inspect buildtsi`. The disk logged
   `lost async page write`, so a file that read back fine may still be stale;
   rebuilding only the shards known to be damaged would leave a new `_series`
   next to indexes that point at old series IDs. A shard with no `index/` is
   opened with the in-memory index — the OOM loop at the top of this file.
5. Dry-run influxd on `127.0.0.1` inside the helper pod, `SHOW SHARDS` checked
   against the shard directories, then the real StatefulSet.

The old PV `pvc-35b64c2f-…` was set to `Retain` and its PVC
`influxdb-data-influxdb-0` left Bound, so neither the provisioner's teardown nor
anything else deletes it; remove both once the disk is replaced.

## Storage — `persistence.existingClaim: influxdb-data-ssd`

The PVC is created by hand, not by the chart's `volumeClaimTemplates`:

```sh
kubectl -n influxdb create -f - <<'PVC'
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: influxdb-data-ssd
  labels: {app.kubernetes.io/instance: influxdb, app.kubernetes.io/name: influxdb}
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: local-ssd
  resources: {requests: {storage: 8Gi}}
PVC
```

With a VCT, a missing PVC is silently re-provisioned empty, influxd starts on
nothing and the databases CronJob creates empty databases — the 2026-07-18
failure again. With `existingClaim` the pod stays `Pending` instead. `local-ssd`
is `Retain`, so deleting the PVC does not delete the data either.

Changing between VCT and `existingClaim` is an immutable StatefulSet change:
delete the StatefulSet (the PVC stays), then sync. The old `ignoreDifferences` on
`/spec/volumeClaimTemplates` went with the VCT.

## This `application.yaml` is applied by hand

[`../bootstrap.yaml`](../bootstrap.yaml) lists `influxdb/application.yaml` in
its `directory.exclude`, so the observability app-of-apps never applies this
file: `kubectl apply -f application.yaml`, then sync the app in the Argo UI.
(The local `argocd` CLI's default context is a different Argo instance.)

As of 2026-09-24 git and live agree again. The earlier drift — git limits
`768Mi`/`300m` against live `2Gi`/`1` — was resolved in favour of live, per the
OOM analysis above. Other deliberate choices:

- **No `syncPolicy.automated`.** Live had none, and a `kubectl scale --replicas=0`
  for maintenance (as in the recovery above) must not be undone by self-heal.
- **Chart pinned to `4.12.1`.** `4.12.5` (Renovate, never deployed) renders
  `flux-enabled = true` in `[http]`, switching on Flux on an unauthenticated,
  LAN-exposed 8086. Bump it as its own change.
- **No `nodeAffinityPreset`.** It was never a value of this chart (a Bitnami
  idiom) and rendered nothing.
- **`INFLUXDB_DATA_COMPACT_THROUGHPUT_BURST=8m` dropped.** It had been patched onto
  the StatefulSet by hand (2026-06-08) and was lost when the StatefulSet was
  recreated. It never took effect anyway: influxd logged the 48 MiB default.
