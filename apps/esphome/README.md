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

## `volumes`/`volumeMounts`: `emptyDir` at `/tmp`

The chart runs the container with `readOnlyRootFilesystem: true` and gives it
nothing writable except `/config`. Compilers and PlatformIO write temp files to
`/tmp`; without a writable `/tmp` a firmware build fails. The chart only renders
`volumes`/`volumeMounts` when `persistence.enabled` is true.

## `resources.requests`

`memory: 256Mi` (chart default 128Mi) covers the idle dashboard and keeps the pod
out of `BestEffort` on a node it shares with Postgres. No `limits`: a compile
spike must not be OOM-killed halfway through.

## Ingress: `esphome.*` behind oauth2-proxy

Since 2026.9 the image's `dashboard` command starts the new ESPHome Device
Builder (`esphome-device-builder`, still port 6052 and `/version` for the chart's
probes). It has no login unless `ESPHOME_USERNAME`/`ESPHOME_PASSWORD` are set,
and the chart has no way to pass env. An Ingress host here is published to
the internet by `networking/keenetic-operator`, and the dashboard can read
`secrets.yaml` and flash firmware to every device, so it sits behind
`mcp/oauth2-proxy` with the same two annotations as `apps/homepage` (see
[`../../mcp/oauth2-proxy/README.md`](../../mcp/oauth2-proxy/README.md) — no
Google Console change needed for a new host).

`proxy-read-timeout`/`proxy-send-timeout: 3600`: compile and log output stream
over a WebSocket that can stay quiet for minutes while the toolchain downloads or
the linker runs; nginx's default 60 s would cut it.

## Devices on the LAN: no mDNS from the pod

The pod runs on the pod network, not `hostNetwork` (the chart has no switch for
it), so `<device>.local` names and mDNS discovery do not work. Online status uses
ping instead (`ESPHOME_DASHBOARD_USE_PING=true`, set by the chart), and OTA needs
an address the pod can reach — give each device a DHCP reservation on the router
and set it in the device YAML:

```yaml
wifi:
  use_address: 192.168.99.x
```
