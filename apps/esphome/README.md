# esphome

The [ESPHome](https://esphome.io) dashboard: edit device YAML, compile firmware and
flash it over the air. Chart `esphome` from `https://charts.jeffresc.dev`
([JeffResc/charts](https://github.com/JeffResc/charts), not maintained by ESPHome
upstream) — a single-replica StatefulSet. Exposed at
`https://esphome.whitediver.keenetic.link` behind Google login.

This file holds the rationale that, by repo convention, must **not** live as
comments inside `application.yaml` (see the root `CLAUDE.md`).

No out-of-band Secrets.

## `image.tag: 2026.9.1`

Chart 0.2.2 defaults to its `appVersion` 2026.4.0, five monthly releases behind.
Pinned to the latest stable for reproducible GitOps; bump deliberately. The
`app.kubernetes.io/version` label still says the chart's `appVersion` — cosmetic.
Renovate tracks the chart version, not this tag.

## `nodeSelector: kube-worker-3` + `persistence.storageClass: local-ssd`

Compiling firmware is the heavy part: PlatformIO toolchains plus a parallel C/C++
build, roughly 1–2 GiB of RAM at peak for an ESP32 target. Only kube-master and
kube-worker-3 have 8 GiB; kube-worker-1/2 have ~830 Mi allocatable. kube-master's
`local-path` volumes sit on a failing USB HDD (see
[`../../platform/local-path/README.md`](../../platform/local-path/README.md)), so
the app goes to worker-3's SSD.

`local-ssd` rather than `local-path` because `/config` is user data (device YAML,
`secrets.yaml`): the class is `Retain`, so deleting the PVC or the Application
does not `rm -rf` it. A released PV stays at
`/mnt/ssd/local-path/pvc-<uid>_esphome_esphome-config` on worker-3. The class
already restricts scheduling to worker-3; the `nodeSelector` just says so
explicitly.

`size: 10Gi` is advisory — `local-path` does not enforce quotas. The PlatformIO
toolchain cache under `/config` grows by several hundred MiB per platform
(ESP8266, ESP32, …).

The PVC (`esphome-config`) is a plain chart template, not a StatefulSet
`volumeClaimTemplate`, so it is owned by the Application.

## `volumes`/`volumeMounts`: `emptyDir` at `/tmp` and `/.cache`

The chart runs the container with `readOnlyRootFilesystem: true` and gives it
nothing writable except `/config`. Two more paths have to be writable for a
firmware build:

- `/tmp` — compiler and PlatformIO temp files.
- `/.cache` — ccache. The pod runs as uid 10099, which has no passwd entry, so
  `HOME=/`, and ESPHome keeps its compiler cache in
  `$HOME/.cache/esphome/platformio-ccache`. Without this mount every object fails
  with `ccache: error: Read-only file system`. The chart has no `env`, so
  `CCACHE_DIR` cannot be pointed elsewhere.

Checked 2026-10-10 with a throwaway ESP8266 build inside the pod: it failed on
ccache alone, and passed once ccache had a writable directory.

Both are `emptyDir`, so they live on worker-3's SD card and are lost when the pod
is recreated — fine for temp files and a compiler cache. `/.cache` has
`sizeLimit: 2Gi` so a growing ccache cannot fill the SD card (it is ~10 MiB per
ESP8266 device). ccache's own cap is 5 GiB, so in theory the limit is reached
first and the kubelet evicts the pod. The cache then starts empty, which costs
only a slower next build. The PlatformIO toolchains themselves are on `/config`
(`.esphome/platformio`, ~0.5 GiB after the first ESP8266 build), so they persist.

The chart only renders `volumes`/`volumeMounts` when `persistence.enabled` is true.

## `resources.requests`

`memory: 256Mi` (chart default 128Mi) covers the idle dashboard and keeps the pod
out of `BestEffort` on a node it shares with Postgres. No `limits`: a compile
spike must not be OOM-killed halfway through.

## Ingress: `esphome.*` behind oauth2-proxy

Since 2026.9 the image's `dashboard` command starts the new ESPHome Device
Builder (`esphome-device-builder`, still port 6052 and `/version` for the chart's
probes). It has no login unless `ESPHOME_USERNAME`/`ESPHOME_PASSWORD` are set,
and the chart has no way to pass env. Every `*.whitediver.keenetic.link` host
is meant to be reachable from the internet through the router's KeenDNS proxy,
and the dashboard can read `secrets.yaml` and flash firmware to every device, so
it sits behind `mcp/oauth2-proxy` with the same two annotations as
`apps/homepage` (see
[`../../mcp/oauth2-proxy/README.md`](../../mcp/oauth2-proxy/README.md) — no
Google Console change needed for a new host).

### Publishing on the router is manual for now

`networking/keenetic-operator` is held at zero replicas (see its
`application.yaml`), so nothing creates the router-side entries for this host.
Until an `ip http proxy esphome` entry exists, the name answers with the
router's own web panel (`<title>KeeneticOS Web Panel</title>`, HTTP 200), not
with this app. Mirror the hand-made entries the other hosts use: `domain ndns`,
upstream `https 192.168.99.44 443`, with `preserve-host` (the Ingress matches on
`Host`). The CLI sequence is in the
[keenetic-operator README](https://github.com/Arbuzov/keenetic-operator#publishing-a-web-app).

`proxy-read-timeout`/`proxy-send-timeout: 3600`: compile and log output stream
over a WebSocket that can stay quiet for minutes while the toolchain downloads or
the linker runs; nginx's default 60 s would cut it.

## Devices on the LAN: no mDNS from the pod

The pod runs on the pod network, not `hostNetwork` (the chart has no switch for
it), so `<device>.local` names and mDNS discovery do not work. Online status uses
ping instead (`ESPHOME_DASHBOARD_USE_PING=true`, set by the chart). Unprivileged
ICMP works from the pod despite `capabilities.drop: [ALL]` (checked 2026-10-10:
`icmplib` with `privileged=False` reaches `192.168.99.1`). OTA needs an address
the pod can reach — give each device a DHCP reservation on the router and set it
in the device YAML:

```yaml
wifi:
  use_address: 192.168.99.x
```
