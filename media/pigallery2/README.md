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

Enabled via the `media` bootstrap `exclude` glob — see
[`media/README.md`](../README.md) for the on/off switch, the cascade
finalizer, and the `Retain` data-safety guarantees on config + photos.

## Replaces photoprism on the same host

The ingress declares `photos.whitediver.keenetic.link` — previously served by
`media/photoprism`, now held back in the same bootstrap swap so the two
ingresses never fight over the hostname at the same time.
