# media/pigallery2

PiGallery2 (`bpatrik/pigallery2`) on `kube-worker-1`. Config on `smb`
(`/app/data/config`); photos mounted read-only from the static
`pigallery2-photos` PV (`//192.168.99.44/pictures`).

Enabled via the `media` bootstrap `exclude` glob — see
[`media/README.md`](../README.md) for the on/off switch, the cascade
finalizer, and the `Retain` data-safety guarantees on config + photos.

## Replaces photoprism on the same host

The ingress declares `photos.whitediver.keenetic.link` — previously served by
`media/photoprism`, now held back in the same bootstrap swap so the two
ingresses never fight over the hostname at the same time.
