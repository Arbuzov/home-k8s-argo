# local-path

Configuration for the cluster's `rancher.io/local-path` provisioner, plus the
`local-ssd` StorageClass that puts Postgres on kube-worker-3's USB SSD.

This app does **not** deploy the provisioner. It is `rancher/local-path-provisioner:v0.0.24` in
`local-path-storage`, installed by hand with `kubectl` in 2023 and still unmanaged.
This app only takes over its `local-path-config` ConfigMap and adds one class.

## `local-path-config` — nodePathMap

| node | path | what backs it |
| --- | --- | --- |
| `DEFAULT_PATH_FOR_NON_LISTED_NODES` | `/tmp` | tmpfs / SD root — why other READMEs call `local-path` unusable |
| `kube-master` | `/srv/kubernetes/local-provisioner` | **not** the SD root: `/srv/kubernetes` → `/mnt/usb/kubernetes`, exFAT on a failing USB HDD (see below) |
| `kube-worker-3` | `/mnt/ssd/local-path` | USB SATA SSD, **added 2026-09-15** |

The first two entries are copied verbatim from the live ConfigMap. The only change is
the `kube-worker-3` line.

- The provisioner hot-reloads `config.json`, so no restart is needed.
- Existing PVs keep the path they were created with. Only **new** volumes on
  kube-worker-3 land on the SSD. That includes plain `local-path` PVCs scheduled
  there, which used to go to `/tmp`.
- Argo now owns the **whole** ConfigMap. Keep all four keys (`config.json`,
  `helperPod.yaml`, `setup`, `teardown`). Dropping one breaks provisioning on every node.

### kube-master's path is a dying disk

`/srv/kubernetes` on kube-master has been a symlink to `/mnt/usb/kubernetes` since
2023, so every `local-path` volume there sits on the exFAT partition of the
2.5" USB "Backup" HDD, next to the SMB-CSI shares and the photoprism `hostPath`
PVs. As of 2026-09-24 that disk is failing: SMART 25 pending sectors, 1.17 M
load cycles, kernel `critical medium error`. It took InfluxDB down (moved to
`local-ssd`, see [`../../observability/influxdb/README.md`](../../observability/influxdb/README.md)).
Still on it: `prometheus-server`, `octoprint`, `octoprint-plugins`,
`jellyfin-config`, `ncc/confd-data`. Do not create new `local-path` volumes on
kube-master until the disk is replaced.

## `local-ssd` StorageClass (for Postgres)

Same provisioner, two deliberate differences from the default `local-path`:

- **`reclaimPolicy: Retain`.** `local-path` is `Delete`: deleting the PVC, or a CNPG
  `Cluster` that owns it, runs `teardown`, which is `rm -rf` of the data. With Retain,
  database data survives unless someone deletes it on purpose. A released PV stays at
  `/mnt/ssd/local-path/pvc-<uid>_<ns>_<pvc>`; delete the directory and the PV by hand.
- **`allowedTopologies: kube-worker-3`.** The class only makes sense where the SSD is.
  With `WaitForFirstConsumer`, the scheduler considers only worker-3 for a pod whose
  unbound PVC uses this class, so a CNPG `Cluster` needs no extra `nodeSelector`.
  An existing selector pointing at another node leaves the pod `Pending`.

## Node side (kube-worker-3) — precondition, not managed here or in Ansible

The config above is only safe once this state exists on the node. **Do not sync
this app before it does**: otherwise the first PVC on worker-3 creates
`/mnt/ssd/local-path` on the SD card.

- **Disk.** Plextor PX-128M5S in an ASMedia `174c:55aa` USB-SATA enclosure, MBR `0xcb54d96b`.
  - `sda1` (32 GiB FAT32 "K8S") and `sda2` (WinRE) are left alone.
  - `sda3` = the former unallocated gap `67110912..248332287` (86.4 GiB), ext4, label `pgdata`.
  - Pre-change partition table dump: `/root/sda-ptable-2026-09-15.sfdisk`.
- **fstab.** `UUID=<sda3> /mnt/ssd ext4 defaults,noatime,nofail,x-systemd.device-timeout=30 0 2`.
  - `nofail`: a missing disk must not stop the node booting.
  - passno `2`: a normal boot fsck.
- **`chattr +i /mnt/ssd`**, set on the empty directory *before* mounting. If the disk
  does not mount, nothing can create `local-path/...` underneath it. Without this,
  the provisioner's `mkdir -p` and kubelet's `hostPath: DirectoryOrCreate` would
  silently put a database on the SD card and hand Postgres an empty directory.
  With it, the pod fails loudly (`ContainerCreating`) until the mount is back.
- **Kernel cmdline** `usb-storage.quirks=174c:55aa:u` in `/boot/firmware/cmdline.txt`
  (backup: `cmdline.txt.bak-2026-09-15`). It keeps the bridge off `uas`, the same fix
  kube-master's backup disk needed.
- **`usb_max_current_enable=1`** under `[all]` in `/boot/firmware/config.txt` (backup:
  `config.txt.bak-2026-09-15`). Without it the Pi 5 caps all USB ports at 600 mA
  combined, and the SSD drops off the bus while writing (see Status below).
- **Gate.** Write 4 GiB of random data, `drop_caches`, and compare md5 on readback.
  This must show **zero** new `I/O error` / `reset … USB` lines in `dmesg`.

### Status 2026-09-15 — gate passed after the power fix

- **First attempt failed under write load on both drivers.** Reads were fine.
  - On `uas`: aborted `WRITE(10)` commands, USB resets and write I/O errors during `mkfs.ext4`.
  - On `usb-storage`: 30 s command timeouts and resets.
- **Cause: the USB power budget.** worker-3's PSU advertises only 900 mA over USB-C
  (`/proc/device-tree/chosen/power/max_current`). With `usb_max_current_enable=0` the
  firmware then gives all USB ports 600 mA combined.
- **With `usb_max_current_enable=1` and a reboot the gate passed.** 4 GiB written at
  117 MB/s and read back at 286 MB/s, md5 identical, zero kernel storage errors.
- **Caveat.** The setting raises the USB cap to 1.6 A on the assumption that the PSU
  can deliver it. Watch `vcgencmd get_throttled` for the under-voltage bits
  (`0x1` / `0x10000`). A powered hub or the official 27 W PSU removes the assumption.

**Never run `systemctl daemon-reload` on kube-worker-3.** systemd 257.13 crashed with
SIGSEGV during a reload and froze PID 1: running pods kept going, new pods went
`Unknown`, and recovery needed a kernel reboot (`sysrq` s → u → b). You don't need a
reload for this mount anyway: `mount /mnt/ssd` reads fstab itself, and the fstab
generator picks the line up at boot.

## Moving a CNPG cluster here

A separate per-DB change, not part of this app. The four clusters (`grafana-pg`,
`litellm-pg`, `n8n-pg`, `vikunja-pg`) sit on static `*-pg-local` PVs. Each move
goes the same way:
1. Take a fresh `pg_dump` (each one already has a backup PVC).
2. Set `storage.storageClass: local-ssd` and drop the hard `nodeSelector`.
3. Rebuild the instance (`kubectl cnpg destroy` or a new `Cluster` bootstrapped from the dump).
