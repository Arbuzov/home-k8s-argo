# metrics-server

`metrics-server` from `https://kubernetes-sigs.github.io/metrics-server/`, the
API backing `kubectl top` and any HPA. Currently **held back** from the
`platform` app-of-apps by its `exclude` glob, so it is applied push-based:

```sh
kubectl apply -f platform/metrics-server/application.yaml
```

See [`../README.md`](../README.md) for how to enable it (the chart repo and the
`kube-system` destination also have to be added to
[`../project.yaml`](../project.yaml) first).

This file holds the rationale that, by repo convention, must **not** live as
comments inside `application.yaml` (see the root [`CLAUDE.md`](../../CLAUDE.md)).

## `nodeSelector: kube-master` — the reason it was added no longer holds

The manifest comment this replaced said the pod is pinned to `kube-master`
because that is the only node with the image cached — `kube-worker-3` could not
reach `registry.k8s.io`, so a pod scheduled there sat in `ImagePullBackOff`.

**That is stale.** All four nodes were verified to reach `registry.k8s.io` on
2026-07-25, during the nginx adoption work — see
[`../nginx/README.md`](../nginx/README.md), which dropped its own image-cache
pin for the same reason and explicitly flagged this note as out of date.

The `nodeSelector` is therefore a leftover, kept only because nothing has
re-tested `metrics-server` without it. `metrics-server` has no placement
requirement of its own. **Removing it is the intended direction** — do it in a
commit that says so, rather than leaving the control plane as a de-facto image
cache. Nothing here justifies the pin any more.

## `--kubelet-insecure-tls`

The kubelet serving certificates on this cluster are not signed by the cluster
CA, so `metrics-server` cannot verify them. The same limitation is why the
Prometheus `kubelet` scrape jobs use `insecure_skip_verify` — see
[`../../observability/prometheus/README.md`](../../observability/prometheus/README.md).
Fixing it properly means enabling kubelet serving-cert rotation cluster-wide;
until then both consumers skip verification.
