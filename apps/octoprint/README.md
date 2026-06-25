# octoprint

OctoPrint pinned to `kube-master`, driving a 3D printer wired to that node
over USB serial. Single replica, `local-path` persistence, exposed via the
nginx ingress at `dev.whitediver.keenetic.link/octoprint`.

This file holds the rationale that, by repo convention, must **not** live as
comments inside `application.yaml` (see the root `CLAUDE.md`).

## `image.tag: 1.11.7`

Pinned for reproducible GitOps; bump deliberately. 1.11.7 is the latest
stable — the floating `latest` tag lags at 1.11.6.

## `device` + `hostDev.enabled: true`

The printer is a CP210x USB-serial board on `kube-master` (appears as
`/dev/ttyUSB0`), which is why this app is pinned there via `nodeSelector`.

`hostDev` bind-mounts the host's live `/dev` (devtmpfs) into the pod, so the
device stays visible even if it (re)appears after the pod starts — no pod
restart on a USB replug. `device: /dev/ttyUSB0` is the legacy single-node
mount and is ignored while `hostDev.enabled` is true.

## `config.overlay` serial — `autoconnect: true`

Auto-detect and connect to the printer on startup. Port/baud are left on
AUTO so a `ttyUSB0`↔`ttyUSB1` re-enumeration still resolves.
