# nginx

The cluster's only ingress controller — official
[`ingress-nginx`](https://kubernetes.github.io/ingress-nginx) chart `4.14.0`,
namespace `nginx`. Every `*.whitediver.keenetic.link` hostname reaches the
cluster through it: the Keenetic router terminates TLS and reverse-proxies
plain HTTP to `192.168.99.44:80`, and this controller routes by `Host`.

## Ingress status advertises the router, not this controller

`publishService.enabled: false` + `extraArgs.publish-status-address: 192.168.99.1`, so
every Ingress reports the **router's** LAN address in `status.loadBalancer`, not
`192.168.99.44`. `kubectl get ingress` showing `192.168.99.1` in `ADDRESS` is the
intended result, not drift.

The one consumer of that status is `../../networking/keenetic-operator`, which turns it
into `ip host <name> <address>` records on the Keenetic. While it published
`192.168.99.44`, LAN clients resolved every `*.whitediver.keenetic.link` name straight
to this controller and bypassed the router, which broke two things at once:

- **TLS.** Only the router holds the Let's Encrypt certificate for
  `whitediver.keenetic.link` (KeenDNS obtained it; the name is not in a zone we can
  issue for). Most Ingresses here carry no `spec.tls`, so a direct hit answers with
  `CN=Kubernetes Ingress Controller Fake Certificate` — a warning on every phone and
  laptop on the LAN, while the same name from outside was fine.
- **`use-forwarded-headers: "true"`.** That key is only safe because nothing but the
  router can reach the controller (see below). A LAN client resolving to
  `192.168.99.44:443` reaches it directly and can therefore spoof `X-Forwarded-*`.

Going through the router costs nothing measurable: a 1.7 MB asset from `photos` pulls at
the same rate over both paths (Wi-Fi is the bottleneck, not the proxy), with about
+100 ms of TTFB.

**A new public hostname needs a matching `ip http proxy` entry on the router.** That
table is hand-maintained (`home`, `dev`, `k8s`, `n8n`, `homepage`, `photos`, `tasks`,
`books`, `litellm`, `otel`, each `upstream … d8:3a:dd:27:94:7d 80`, i.e. `kube-master`).
A name that is missing from it does not fall through to this controller — the router
answers `200` with its own web UI, which looks like the app is broken in a confusing
way. Add the router entry when you add the Ingress.

## Adopted from a hand-run Helm release (2026-07-25)

This release was installed with the Helm CLI and lived outside Argo for 441
days. Adopting it was not a matter of copying `helm get values` into an
Application: the **live objects had drifted from the release**, and syncing
the release's own values with `selfHeal` on would have broken the gateway.

| what | the Helm release said | the live cluster had | this Application |
| --- | --- | --- | --- |
| ConfigMap keys | 2 | **19** | 17 (the 2 snippet keys dropped, see below) |
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

## The 17 ConfigMap keys are load-bearing — do not trim them

The chart itself renders only the two snippet-related keys (and now not even
those, see below). All 17 remaining ones existed **only** as a manual edit of
the live ConfigMap. With `selfHeal: true` they must be declared here or the
next sync silently deletes them, and then:

- `proxy-body-size: 100m` — without it uploads cap at nginx's 1 MB default.
- `use-forwarded-headers: "true"` — the router terminates TLS and proxies
  plain HTTP, so without this backends derive `http://` from the request
  scheme and OAuth `redirect_uri` breaks. A per-ingress
  `configuration-snippet` does **not** work here; ingress-nginx sets
  `$pass_access_scheme` internally and overrides the snippet. This makes the
  controller **trust client-supplied `X-Forwarded-*` headers**, which is only
  safe because nothing but the router can reach it. Keep
  `use-proxy-protocol` disabled — enabling both without a trusted L4 path in
  front turns this into header spoofing.
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

```text
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
registry.k8s.io" claim is stale; it now lives in
[`../metrics-server/README.md`](../metrics-server/README.md), marked as such,
and `metrics-server`'s own `nodeSelector` is a leftover awaiting removal).

Pod *placement* no longer matters for traffic: the Service is a
`LoadBalancer` with `externalIPs: [192.168.99.44]`, so packets arrive at
`kube-master` and kube-proxy forwards them to whichever endpoints exist,
wherever those pods run.

**`kube-master` itself is still a hard single point of failure**, though, and
two replicas do not fix that. `192.168.99.44` is `kube-master`'s own `wlan0`
address, not a floating VIP — there is no failover for it. If that node goes
down, every ingress hostname is unreachable no matter how many controller pods
are healthy elsewhere. What `replicaCount: 2` buys is survival of a *pod* or
*non-master node* failure, which is exactly the class of outage that happened.
Removing the remaining entry-point SPOF needs a real bare-metal
LoadBalancer (MetalLB in L2 mode, or keepalived holding a VIP) and is a
separate change.

### Placement after a rolling update

The `nodeAffinity` preference only applies when a pod is *scheduled*; nothing
rebalances afterwards. During a rolling update the required `podAntiAffinity`
pushes each new pod onto a node the outgoing pods do not occupy — so a roll
can legitimately end with both replicas on the 1 GB nodes while the preferred
8 GB nodes sit empty. It is not broken, just suboptimal. To rebalance, delete
one pod at a time and let the scheduler re-place it:

```sh
kubectl --context kubernetes-local -n nginx delete pod <one-controller-pod>
```

## Image pinned to v1.12.0

Chart `4.14.0` would default to controller `v1.14.0`. The pin (plus both
digests) keeps the exact image that has been running; bumping it is a
separate, deliberate change.

## Snippet annotations are off

The adopted release ran `allowSnippetAnnotations: true` with an empty
`annotation-value-word-blocklist`, and the validating webhook disabled. That
combination lets anyone able to create an `Ingress` run arbitrary Lua or
`load_module` inside the controller pod — there is nothing left to stop it.

Nothing was using it: 0 of the cluster's 16 Ingresses carry a
`*-snippet` annotation. So both keys are gone and `allowSnippetAnnotations`
is back to the chart default of `false`. This is less config, not more, and
it is the one deliberate behaviour change beyond a faithful adoption.

If a future Ingress genuinely needs a snippet, flip `allowSnippetAnnotations`
back to `true` **and** set a real `annotation-value-word-blocklist` at the
same time — do not restore the empty one.

### Removing a key from the values was not enough

Deleting the two keys here left `allow-snippet-annotations: "true"` **still
live** on the ConfigMap, so the hardening silently did nothing until it was
removed by hand. Server-side apply prunes only the fields the *applying*
manager owns, and that key belonged to a different one:

```text
manager=argocd-controller  op=Apply   keys=[client-body-buffer-size, ... 17 keys]
manager=node-fetch         op=Update  keys=[allow-snippet-annotations]
manager=kubectl-patch      op=Update  keys=[... the 17 tuning keys]
```

`node-fetch` is a web UI (Argo CD's own UI / Lens-style client) doing an
`Update`, not an `Apply`. Argo's apply simply never mentions that key, so
nothing removes it, and Argo still reports **Synced** — a removed key is
invisible to the diff because Argo only compares what it manages.

One-time fix, after which desired and live agree and nothing re-adds it:

```sh
kubectl --context kubernetes-local -n nginx patch cm nginx-ingress-nginx-controller \
  --type=json -p '[{"op":"remove","path":"/data/allow-snippet-annotations"}]'
```

The GitOps-native way to avoid the manual step is two commits: first declare
the key here with its current value and sync, so `argocd-controller` takes
ownership of it, then delete it in a second commit — now the prune is Argo's
and needs no `kubectl`.

**Whenever you delete a key from `controller.config`, verify it actually left
the live ConfigMap.** Adding a key works fine; removing one does not, until
whichever manager owns it is gone. The Deployment did not have this problem —
`nodeSelector` is a field Argo owns outright, so dropping the `kube-worker-2`
pin took effect on the first sync.

## `metrics.enabled: true`, but the Service stays unannotated

The controller exposes Prometheus metrics on `:10254`, which is what the MCP
fleet dashboard is built on — per-Ingress status codes, response sizes, request
latency and TLS expiry.

The chart's `controller.metrics.service.annotations` is left empty **on
purpose**. Adding `prometheus.io/scrape: "true"` would hand the target to the
chart's default `kubernetes-service-endpoints` job, which carries no
`metric_relabel_configs` — every nginx metric family would land in a TSDB
running on a Raspberry Pi. A dedicated `ingress-nginx` scrape job with a
keep-list does the scraping instead; see
[`../../observability/prometheus/README.md`](../../observability/prometheus/README.md).

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

## The Helm release history is gone — on purpose

Argo renders the chart with `helm template` and applies it directly; it never
uses Helm's release machinery. That left ten stale `sh.helm.release.v1.nginx.*`
secrets in namespace `nginx`, and `helm list -n nginx` still reported a
`deployed` release that nothing updated — a footgun, because a later
`helm upgrade`/`helm uninstall nginx` would have fought Argo for the same
objects, or deleted them outright.

They were removed as part of this adoption:

```sh
kubectl --context kubernetes-local -n nginx delete secret -l owner=helm,name=nginx
```

`helm list -n nginx` is now empty. There is no `helm rollback` for this
release any more — git is the rollback mechanism. **Never `helm install`
into this namespace again**; change values in `application.yaml`.

## Smoke test

```sh
kubectl --context kubernetes-local -n nginx get pod -o wide     # 2 pods, different nodes
kubectl --context kubernetes-local -n nginx get cm nginx-ingress-nginx-controller \
  -o jsonpath='{.data}' | jq 'length'                           # must stay 17
curl -sI https://dev.whitediver.keenetic.link/                  # 404 + valid TLS = the router -> controller path works
```
