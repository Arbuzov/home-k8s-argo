# nginx

The cluster's only ingress controller — official
[`ingress-nginx`](https://kubernetes.github.io/ingress-nginx) chart `4.14.0`,
namespace `nginx`. Every `*.whitediver.keenetic.link` hostname reaches the
cluster through it: the Keenetic router terminates TLS and reverse-proxies
plain HTTP to `192.168.99.44:80`, and this controller routes by `Host`.

## Adopted from a hand-run Helm release (2026-07-25)

This release was installed with the Helm CLI and lived outside Argo for 441
days. Adopting it was not a matter of copying `helm get values` into an
Application: the **live objects had drifted from the release**, and syncing
the release's own values with `selfHeal` on would have broken the gateway.

| what | the Helm release said | the live cluster had | this Application |
| --- | --- | --- | --- |
| ConfigMap keys | 2 | **19** | all 19 |
| `resources` | `100m`/`128Mi` req+lim | `150m`/`128Mi` req, `500m`/`320Mi` lim | live values |
| liveness/readiness | 5–6 failures, 10s, 1s | 6 failures, 15s, 5s | live values |
| `nodeSelector` | OS only | **pinned to `kube-worker-2`** | removed |
| `replicas` | 1 | 1 | **2** |

Everything in the "live cluster" column was a `kubectl` patch nobody had
folded back into the release. It is all expressed in the Application now, so
the hand-patching era is over — change values here, not with `kubectl`.

Verified before the first sync by rendering `ingress-nginx-4.14.0` locally
with these values and diffing every container and pod-level field against the
live Deployment: zero unintended differences. The only deltas were the
intended ones — `replicas`, the node pin, the affinity block, and the new
PodDisruptionBudget the chart emits once `replicaCount > 1`.

## The 19 ConfigMap keys are load-bearing — do not trim them

The chart itself renders only `allow-snippet-annotations` and
`annotation-value-word-blocklist`. The other 17 existed **only** as a manual
edit of the live ConfigMap. With `selfHeal: true` they must be declared here
or the next sync silently deletes them, and then:

- `proxy-body-size: 100m` — without it uploads cap at nginx's 1 MB default.
- `use-forwarded-headers: "true"` — the router terminates TLS and proxies
  plain HTTP, so without this backends derive `http://` from the request
  scheme and OAuth `redirect_uri` breaks. A per-ingress
  `configuration-snippet` does **not** work here; ingress-nginx sets
  `$pass_access_scheme` internally and overrides the snippet.
- the `300` second `proxy-*-timeout` / `keepalive-*` values — anything slower
  than nginx's 60s default (LLM reasoning calls, large restores) 504s without
  them. Note the per-ingress annotations on `ai/litellm` raise its own
  timeouts to 600s on top of this baseline.
- the gzip and buffer sizes — tuning for the Raspberry Pi nodes.

## Resources and probes come from the live Deployment, not the chart

`128Mi` as a *limit* (what the release carried) OOM-kills this controller. The
live values — `150m`/`128Mi` requested, `500m`/`320Mi` limit — are what has
actually been keeping it alive, so those are what is declared.

The probes are relaxed to 6 failures / 15s period / 5s timeout. The chart
defaults (10s period, 1s timeout) trip on a Raspberry Pi under load and
restart a healthy controller.

## Why `replicaCount: 2` — the outage that caused it

The Deployment was `replicas: 1` hard-pinned to `kube-worker-2` by a manual
patch, because that node was the only one with the controller image cached.

On 2026-07-25 `kube-worker-2` lost Wi-Fi (its brcmfmac firmware wedged after
an AP channel change — `brcmf_proto_bcdc_query_dcmd ... status -110`) and went
`NotReady`. The pod could not reschedule anywhere:

```
0/4 nodes are available: 1 node(s) had untolerated taint(s),
3 node(s) didn't match Pod's node affinity/selector.
```

Every `*.whitediver.keenetic.link` host returned **502** until the node was
recovered by hand — including the MCP gateway, so the tooling used to
diagnose it was down too. One Raspberry Pi on Wi-Fi was a single point of
failure for the whole public surface.

Two replicas with a **hard** `podAntiAffinity` on `kubernetes.io/hostname`
now guarantee they never share a node. The `nodeAffinity` is deliberately
**soft** (`preferred`, weight 100) toward the 8 GB nodes `kube-master` and
`kube-worker-3` — same idiom as `../cnpg-operator`, and for the same reason:
a hard node pin is what caused the outage in the first place, so a replica
must still be able to land on a 1 GB node (`kube-worker-1`, `kube-worker-2`)
rather than sit `Pending`.

The image-cache argument for the pin no longer holds: all four nodes reach
`registry.k8s.io` (checked 2026-07-25 — the "worker-3 can't reach
registry.k8s.io" note in `../metrics-server/application.yaml` is stale).

Traffic entry does not depend on placement: the Service is a `LoadBalancer`
with `externalIPs: [192.168.99.44]`, so packets always arrive at
`kube-master` and kube-proxy forwards them to whichever endpoints exist.

## Image pinned to v1.12.0

Chart `4.14.0` would default to controller `v1.14.0`. The pin (plus both
digests) keeps the exact image that has been running; bumping it is a
separate, deliberate change.

## `admissionWebhooks.enabled: false`

Inherited from the original release and kept. The validating webhook adds a
failure mode — a wedged webhook pod rejects every Ingress write — for little
benefit on a single-tenant home cluster.

## Project wiring

`platform/project.yaml` needed two additions for this app to be legal:

- `sourceRepos` += `https://kubernetes.github.io/ingress-nginx`
- `destinations` += namespace `nginx` (AppProject destinations are an
  explicit allow-list; without it the sync is rejected)

`clusterResourceWhitelist` is already `*/*`, which covers the ClusterRole,
ClusterRoleBinding and IngressClass the chart ships.

## Sync options

`ServerSideApply=true` + `ServerSideDiff=true` — same idiom as
`../cnpg-operator` and `../arc-operator`; needed to take over fields from the
old Helm field manager without conflicts.

## Leftover: the orphaned Helm release secret

Argo renders the chart with `helm template` and applies it directly; it does
not use Helm's release machinery. The old `sh.helm.release.v1.nginx.*` secrets
in namespace `nginx` are therefore stale, and `helm list -n nginx` still shows
a `deployed` release that nothing updates. That is a footgun — a later
`helm upgrade nginx` would fight Argo for the same objects. Clean up with:

```sh
kubectl --context kubernetes-local -n nginx delete secret -l owner=helm,name=nginx
```

## Smoke test

```sh
kubectl --context kubernetes-local -n nginx get pod -o wide     # 2 pods, different nodes
kubectl --context kubernetes-local -n nginx get cm nginx-ingress-nginx-controller \
  -o jsonpath='{.data}' | jq 'length'                           # must stay 19
curl -sI https://dev.whitediver.keenetic.link/                  # 404 from the default backend + valid TLS = path works
```
