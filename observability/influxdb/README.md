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

## `ignoreDifferences` — StatefulSet `volumeClaimTemplates`

The chart renders `volumeClaimTemplates` with `annotations: null`; the
API server stores them as absent, so Argo CD reports a permanent diff.
VCTs are immutable on a StatefulSet anyway, so there's nothing Argo CD
could reconcile — the diff is ignored.
