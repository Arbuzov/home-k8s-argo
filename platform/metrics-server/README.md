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

## `nodeSelector: kube-master`

The pod is pinned to `kube-master` because that is the only node where the image
is cached — `kube-worker-3` cannot reach `registry.k8s.io` on this network, so a
pod scheduled there sits in `ImagePullBackOff`.

This is a workaround for the egress restriction, not a placement requirement:
`metrics-server` itself is happy on any node. If the image is ever mirrored
somewhere reachable from every node (or pre-pulled onto them), drop the
`nodeSelector` rather than keeping the control plane as a de-facto image cache.

## `--kubelet-insecure-tls`

The kubelet serving certificates on this cluster are not signed by the cluster
CA, so `metrics-server` cannot verify them. The same limitation is why the
Prometheus `kubelet` scrape jobs use `insecure_skip_verify` — see
[`../../observability/prometheus/README.md`](../../observability/prometheus/README.md).
Fixing it properly means enabling kubelet serving-cert rotation cluster-wide;
until then both consumers skip verification.
