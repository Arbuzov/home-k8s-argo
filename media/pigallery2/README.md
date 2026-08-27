# media/pigallery2

PiGallery2 (`bpatrik/pigallery2`). Config on `smb`
(`/app/data/config`); photos mounted read-only from the static
`pigallery2-photos` PV (`//192.168.99.44/pictures`).

## Node placement & memory

`nodeSelector` **temporarily** parks the pod on `kube-worker-3` (8Gi, spare
headroom): its home board `kube-worker-1` (~900Mi) is already over its
memory-limit budget. Move it back once the node-rebalancing pass frees room on
worker-1.

The memory **limit is 1Gi**, not the initial 512Mi: 512Mi OOMKilled on every
attempt during the startup library scan ("running diagnostics…") of the 200Gi
photos share.

The memory **request is 512Mi**, raised from 128Mi to match the measured
steady-state draw of ~408Mi. An under-sized request lets the scheduler pack the
node as if this pod were a quarter of its real size, and memory is not
compressible — the request is what keeps it from being first in line for
eviction.

## The SQLite index lives on a node-local `hostPath`

The `db` volume is `hostPath: /var/lib/pigallery2-db` with
`DirectoryOrCreate`, which is safe only because the pod is pinned to
`kube-worker-3` by the `nodeSelector` above. **The two go together — moving the
pod without moving or re-creating this directory loses the index.**

Neither alternative works here:

- **not `smb`** — SQLite over CIFS with `nobrl` is lock-fragile, the same class
  of problem that drove Grafana off SMB (see
  [`../../observability/grafana/README.md`](../../observability/grafana/README.md)).
- **not `local-path`** — its *default* `nodePathMap` sends non-master nodes to
  `/tmp`, which is wiped on reboot. That looks like it works right up until the
  node restarts.

`hostPath` survives pod and node restarts, so the library index is not rebuilt
from the 200Gi share every time. Losing it is not data loss (it is a derived
index) but it is a long, I/O-heavy rescan.

Enabled via the `media` bootstrap `exclude` glob — see
[`media/README.md`](../README.md) for the on/off switch, the cascade
finalizer, and the `Retain` data-safety guarantees on config + photos.

## Replaces photoprism on the same host

The ingress declares `photos.whitediver.keenetic.link` — previously served by
`media/photoprism`, now held back in the same bootstrap swap so the two
ingresses never fight over the hostname at the same time.
