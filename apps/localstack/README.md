# localstack

A local AWS cloud stack for testing against S3/SQS/Lambda/etc. without a real
AWS account. Deployed from the official chart at `https://helm.localstack.cloud`
(chart `localstack`, pinned to `0.7.0`).

This file holds the rationale that, by repo convention, must **not** live as
comments inside `application.yaml` (see the root `CLAUDE.md`).

## `image.repository: localstack/localstack`

The chart's default image is `localstack/localstack-pro`, which requires a
`LOCALSTACK_AUTH_TOKEN` (a paid license) to start at all. This repo carries no
credentials, so the image is switched to the free community edition instead —
no auth token, no Secret needed.

## `extraEnvVars: LOCALSTACK_AUTH_TOKEN`

Wired to the `localstack` Secret (`kubectl create secret generic localstack
--from-literal=LOCALSTACK_AUTH_TOKEN=<token> -n localstack`). Not required to
run the community image, but activates the freemium license tier (extra
service coverage) once the token is present.

## `resources.requests` and long probe tolerances

Both workers are Raspberry Pi-class arm64 nodes with ~830Mi allocatable RAM
total, shared across every pod on the node. On first deploy, LocalStack was
stuck in `CrashLoopBackOff`: the chart's default liveness/readiness probes
(10s initial delay, 5 failures × 10s ≈ 60s tolerance) killed the container
mid-boot every time — a single internal init step
(`_resolve_api_provider_specs`) alone took 25-27s, and even a first relaxed
attempt (~120s tolerance) still wasn't enough under node contention.

Fixed with two changes together:

- `resources.requests: {cpu: 250m, memory: 256Mi}` — moves the pod off
  `BestEffort` QoS so it isn't starved of CPU/memory by everything else
  scheduled on the same Pi. No `limits` set, so a slow cold boot isn't at
  risk of being OOM-killed while initializing.
- `livenessProbe`/`readinessProbe` relaxed to a ~10 minute tolerance
  (`initialDelaySeconds: 60`, `periodSeconds: 15`, `failureThreshold: 36`) —
  generous headroom instead of guessing the exact boot time.

The chart has no `startupProbe` field (checked `templates/deployment.yaml` in
`localstack` 0.7.0 — only `livenessProbe`/`readinessProbe` are templated),
so tuning those two is the only lever available.

## No ingress, no persistence

Left at chart defaults: `service.type: NodePort` (edge service on node port
`31566`, the standard way to point an AWS SDK/CLI at an in-cluster
LocalStack), and `persistence.enabled: false` — state resets on pod restart,
which is normal for a throwaway test double.

## `extraDeploy`: a second Service for the cluster's external IP

`192.168.99.44` (`kube-master`'s own `wlan0` address — see
`../../platform/nginx/README.md`) is how this cluster publishes services to
the LAN, since there is no MetalLB or other LoadBalancer controller. The
mechanism kube-proxy actually honors is `spec.externalIPs`, not
`loadBalancerIP` — see `../../platform/argo-cd/README.md`.

The `localstack` chart's own `templates/service.yaml` doesn't template
`externalIPs` at all (checked `0.7.0`), so `service.type`/`service.loadBalancerIP`
values alone can't get traffic to bind there. Instead of forking the chart's
Service, a second plain `Service` named `localstack-external` is injected via
the chart's Bitnami-style `extraDeploy` list (same escape hatch used for the
`n8n` chart's `extraManifests`), selecting the same pod labels
(`app.kubernetes.io/name/instance: localstack`) and re-exposing the edge port
on `4566` — the LocalStack default AWS endpoint port — via `externalIPs:
[192.168.99.44]`. So `awslocal --endpoint-url http://192.168.99.44:4566 ...`
works from any LAN host, alongside the existing in-cluster/NodePort paths.
