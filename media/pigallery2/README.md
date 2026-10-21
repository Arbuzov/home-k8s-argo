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

## Thumbnail cache on worker-3's USB SSD

`tmp` (`/app/data/tmp`: thumbnails, converted photos/videos) is
`hostPath: /mnt/ssd/pigallery2-tmp` on the USB SATA SSD, not an `emptyDir`:
an `emptyDir` threw every generated thumbnail away on each pod restart, so
every folder was slow again on its first view. Same `nodeSelector` coupling as
`db`. It is derived data: if `/mnt/ssd` is not mounted, the pod either waits
(`chattr +i` on the mountpoint) or caches on the SD card — no data loss either
way. Disk and mount details:
[`platform/local-path`](../../platform/local-path/README.md#node-side-kube-worker-3--precondition-not-managed-here-or-in-ansible).

## Object names are pinned with `forceRename`

`pigallery2-config` and the static `pigallery2-photos` PV/PVC carry an explicit
`forceRename`. app-template 4+ has no `nameOverride` and appends the item key to
the release name only when there is more than one item of that kind. Unpinned,
the config claim (the only PVC the chart provisions; `photos` is an
`existingClaim`) would become plain `pigallery2`: the app would start on an
empty config, and `prune: true` would delete `pigallery2-config`, leaving the
old config only as a `Released` PV (`smb` is `Retain`) to recover by hand.
The photos names are referenced elsewhere too — the PVC by
`persistence.photos.existingClaim`, the PV by the PVC's `volumeName`.

## Replaces photoprism on the same host

The ingress declares `photos.whitediver.keenetic.link` — previously served by
`media/photoprism`, now held back in the same bootstrap swap so the two
ingresses never fight over the hostname at the same time.

## app-template 5: the Deployment is recreated once (2026-09-24)

The chart's selector label changed and a Deployment selector is immutable, so
the bump goes in two commits: `replicas: 0` plus a temporary
`Force=true,Replace=true` sync option, then replicas restored and the option
dropped together. Zero replicas keeps two instances from opening the `hostPath`
SQLite index and the SMB config at once — the forced delete does not wait for
the old pod. Mechanism:
[`mcp/basic-memory`](../../mcp/basic-memory/README.md#app-template-5-the-deployment-is-recreated-once-2026-09-24).

## Held at `replicas: 0` until the master's HDD is replaced (2026-10-21)

The photos share lives on `kube-master`'s USB HDD (`/mnt/usb/kubernetes/nfs/pictures`,
`/dev/sda4`, exFAT) — the same disk behind every SMB-CSI volume in the cluster, and it is
failing: SMART `Current_Pending_Sector` 38, 45k power-on hours. A photo-converting job's
`ffprobe` hit one unreadable sector near the end of
`2019/2019-10-20/HERO7 BLACK 1/GH013758.MP4` (LBA 107797896, inside the file per FIBMAP)
and sat in `D` state while the read was retried every ~2 s. Each retry stalled the disk
queue for all SMB clients: other apps' `soft` mounts returned `EAGAIN` on plain directory
reads, basic-memory's MCP `initialize` took 59 s, and litellm listed 0 tools for it.

The sector was rewritten with zeros (`dd if=/dev/zero of=/dev/sda bs=4096
seek=13474737 count=1 oflag=direct`), so that video now has 4 KiB of zeros near its end.
Pending went 38 → 37 with no reallocation — the sector took the write in place and may
fail again. The other 37 pending sectors are still somewhere on the disk, and a library
scan or conversion pass reads the whole 200Gi share, which on this disk is a surface scan
that will find them one stall at a time. Bring the app back once the photos sit on a
healthy disk; the `hostPath` index and the SSD thumbnail cache survive the pause.

Excluding the bad folders is not enough. `Indexing.excludeFolderList` in `config.json`
(on the `pigallery2-config` PVC, not in GitOps) has listed `2019/2019-10-20` and
`2019/2019-10-21` since 2026-10-20, and the converting job opened `GH013758.MP4` anyway: the
exclusion only stops indexing, while the jobs walk media that is already in the DB.
