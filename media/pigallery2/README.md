# media/pigallery2

PiGallery2 (`bpatrik/pigallery2`) on `kube-worker-1`. Config on `smb`
(`/app/data/config`); photos mounted read-only from the static
`pigallery2-photos` PV (`//192.168.99.44/pictures`).

Held back from the cluster via the `media` bootstrap `exclude` glob — see
[`media/README.md`](../README.md) for the on/off switch, the cascade
finalizer, and the `Retain` data-safety guarantees on config + photos.

## Ingress host clashes with photoprism

The ingress declares `photos.whitediver.keenetic.link`, but
`media/photoprism` currently serves that host. This is only harmless
because pigallery2 is **not deployed** (held back), so the ingress is
dormant. Before re-enabling pigallery2, move the host off photoprism —
otherwise the two ingresses fight over the same hostname.
