# mcp/kubernetes

MCP server that manages **this** cluster's Kubernetes API — deployed into the
`mcp` namespace and reconciled by the `mcp` app-of-apps.

## What it runs

[`ghcr.io/containers/kubernetes-mcp-server`](https://github.com/containers/kubernetes-mcp-server)
(the Go server), via the `mcp-kubernetes` chart in the private `home-k8s-helm`
repo. Argo consumes that chart **by git path** (not a Helm registry) — the same
way the `networking/*` charts do — because `home-k8s-helm` is private with no
GitHub Pages: [`application.yaml`](application.yaml) sets
`repoURL: git@github.com:Arbuzov/home-k8s-helm.git`, `path: arbuzov/mcp/kubernetes`,
`targetRevision: <commit SHA>`. Unlike the `networking/*` charts (which track
`HEAD`), this one **pins an immutable commit SHA** — deliberately, because it is
a cluster-admin-capable workload, so an unrelated push to `home-k8s-helm` master
must never auto-redeploy it. (Argo already holds the SSH repo credential for that repo
from the networking charts; no new secret is needed. The chart is intentionally
standalone — no `mcp-library` subchart dependency — so it renders from the git
tree without a dependency build.) The chart starts the server with `--port=8080`,
so it serves **Streamable HTTP at `/mcp`, SSE at `/sse`, and `/healthz`** (the
liveness/readiness probe target). In-cluster it authenticates to the API server
with its own pod ServiceAccount — no kubeconfig.

## Why it was held back until now

The manifest existed but sat in the app-of-apps `exclude` glob because the
chart it referenced (`arbuzov.github.io/mcp-helm/mcp-kubernetes`) launched the
Docker Hub `mcp/kubernetes` image — a *different*, Node-based server that only
speaks **stdio**. With no HTTP listener it failed the chart's HTTP probe and
CrashLooped under Argo CD. The fix was to rebuild the chart in `home-k8s-helm`
around the Go server (which serves HTTP natively), fix the probe to `/healthz`,
and point this Application at the new chart. See the chart's own README under
`home-k8s-helm/arbuzov/mcp/kubernetes/` for the chart-side detail.

## RBAC — this deployment can manage the whole cluster

`application.yaml` grants the ServiceAccount a cluster-wide ClusterRole with
`apiGroups/resources/verbs: ["*"]` and runs the server with `readOnly: false`,
so it can **create/update/delete any resource in the cluster** (effectively
cluster-admin, minus non-resource URLs). That is deliberate — the point of this
server is to manage the cluster — but it is a real blast radius: anything that
reaches the pod (through the basic-auth ingress, or in-cluster) can mutate
everything.

**Destructive operations are intentionally allowed (owner decision, 2026-07-11).**
Do **not** "harden" this by adding `--disable-destructive` or dropping write
verbs — automated reviewers (Sourcery, model councils) flag the blast radius on
every pass, but full read-write including delete is the accepted trade-off here.

Should you ever want to narrow it, two independent dials do so:

- **API side** — replace the `rbac.clusterRole.rules` in `application.yaml` with
  an explicit allow-list, or drop the write verbs for read-only.
- **Server side** — set `readOnly: true` (only read tools exposed) or add
  `extraArgs: ["--disable-destructive"]` (keeps create/update, blocks deletes).

Both must be permissive for a write to succeed, so tightening either one is a
safe brake.

## Access & secrets

Fronted by litellm's MCP gateway, like the other `/mcp/*` servers — the server
itself has **no direct ingress** (`ingress.enabled` stays at the chart default,
`false`); litellm reaches it in-cluster at
`http://mcp-kubernetes.mcp.svc.cluster.local:8080/mcp`.

- **litellm** ([`ai/litellm/application.yaml`](../../ai/litellm/application.yaml)):
  registered under `proxy_config.mcp_servers` as alias `kubernetes`
  (`transport: http`, `allow_all_keys: true`).
- **Public URL** `https://dev.whitediver.keenetic.link/mcp/kubernetes` is routed
  by the shared [`mcp-gateway` ingress](../mcp-gateway/gateway-ingress.yaml) →
  `oathkeeper-proxy:4455` → litellm `/mcp/kubernetes` → this server. nginx
  basic-auth (`mcp-basic-auth`) guards it; Oathkeeper injects litellm's
  `x-litellm-api-key` so tool calls land in litellm's usage stats.
- **Owner step (out-of-band):** Oathkeeper needs a per-alias rule matching
  `/mcp/kubernetes` in the `oathkeeper-rules` Secret (it embeds the litellm
  master key, so it isn't in git) — see
  [`platform/oathkeeper/README.md`](../../platform/oathkeeper/README.md). Until it
  exists the public URL fails at Oathkeeper; in-cluster litellm access still works.
- Cluster API access is via the pod ServiceAccount (RBAC above), not a mounted
  credential.

## Changing the chart

The chart lives in `home-k8s-helm` (`arbuzov/mcp/kubernetes`). Because
`targetRevision` here is a **pinned commit SHA**, shipping a chart change is two
steps: (1) commit + push it to `home-k8s-helm`'s default branch (`master`), then
(2) update `targetRevision` in [`application.yaml`](application.yaml) to the new
commit SHA. Argo won't pick up a chart edit until that SHA is bumped — that's the
point of pinning a cluster-admin workload. Keep the chart standalone (don't
re-add the `mcp-library` dependency): a git-path chart with a `file://`
dependency won't render, since the built subchart and `Chart.lock` are
git-ignored.
