# keenetic-operator

Syncs cluster Ingress hosts to `ip host` records on the Keenetic router over SSH, so
`*.whitediver.keenetic.link` resolves inside the LAN, and to `ip http proxy` entries — the
router's "Domain name -> Access to web applications" table — so the same hosts are reachable
from the internet. Neither has to be edited on the router by hand any more.

The second half is the one that fails quietly. KeenDNS resolves every subdomain of the zone
to the router, so a host with a DNS record and no publication does not break: the router
answers on that name with its own web interface instead of the service. Source: [Arbuzov/keenetic-operator](https://github.com/Arbuzov/keenetic-operator),
chart at `arbuzov/networking/keenetic-operator` in home-k8s-helm.

Picked up by the `networking` app-of-apps automatically — unlike `openconnect-gateway` and
`wstunnel`, this Application carries no private endpoints, so it does not need the
bootstrap `exclude` treatment.

## Credentials

The router SSH login is **not** in this repo. The chart is pointed at an existing Secret
via `keenetic.existingSecret`, created out of band:

```sh
kubectl --context kubernetes-local -n keenetic-operator create secret generic keenetic-operator-creds \
  --from-literal=KEENETIC_USER=admin \
  --from-literal=KEENETIC_PASSWORD='<router-password>'
```

The namespace is created by the Application (`CreateNamespace=true`), so apply the Secret
after the first sync — or create the namespace first, otherwise the Secret has nowhere to
land and the manager pod stays in `CreateContainerConfigError`.

## Values set here

- `keenetic.host` — the router's SSH endpoint. `192.168.99.1:22`.
- `keenetic.defaultIngressIP` — `192.168.99.1`, the router itself. Only a fallback:
  every Ingress in this cluster already reports that address in `status.loadBalancer`,
  so the operator normally reads it from there. It is set to the same value the
  Ingresses publish so that the fallback path cannot quietly disagree with them.
- `publish.upstreamIP` — `192.168.99.44`, ingress-nginx. This is where the router
  *forwards* a published request, and it is deliberately not `defaultIngressIP`: the name
  has to resolve to the router (so the request enters its KeenDNS proxy and gets the
  certificate), and only then does the proxy hand it to ingress-nginx. The two addresses
  describe opposite ends of the same hop. Empty would keep the operator DNS-only, which is
  what it did before chart 0.2.0.
- `publish.upstreamScheme: https`, `publish.upstreamPort: 443` — the fallback when the
  operator cannot derive the scheme from an Ingress's TLS. Several Ingresses on `dev.*`
  carry ssl-redirect, so a plain `:80` upstream answers 308 → `https://dev...`, which
  resolves back to the router, which proxies to `:80` again: a redirect loop that takes
  out Argo CD, Grafana, Prometheus and every MCP server at once.
- `publish.keenDNSZone` — `whitediver.keenetic.link`. Hosts one label below it are
  published with `domain ndns`, the only form that claims a name in KeenDNS. Without it
  the operator writes `domain static whitediver.keenetic.link`, and inside the router's
  own zone that entry does not just fail to publish — it answers for the bare zone. On
  2026-08-30 `whitediver.keenetic.link` itself got proxied to ingress-nginx, every
  request failed upstream, and the router's lockout policy (`ip http lockout-policy 5 15 3`)
  kept banning the client for 15 minutes at a time, taking the web panel down with it.
- `replicaCount` is not set: the chart default, 1. It was held at 0 from 2026-08-30 to
  2026-10-12 — see the next section for why the zone alone did not fix it.
- `nodeSelector` — pinned to `kube-master` like the other singletons. Leader election
  means a second replica would be safe, but only one ever writes to the router, so
  there is nothing to gain from spreading it.

## CRDs are applied by hand

Argo CD excludes `CustomResourceDefinition` globally (`resource.exclusions` in
`argocd-cm`), so this Application never installs or updates the chart's `crds/`. After
every chart bump, compare and apply them from a home-k8s-helm checkout:

```sh
kubectl --context kubernetes-local diff  -f arbuzov/networking/keenetic-operator/crds/
kubectl --context kubernetes-local apply -f arbuzov/networking/keenetic-operator/crds/
```

Skipping this fails silently, and here destructively. Chart 0.3.0 added
`KeeneticWebApp.spec.ndns`, but the cluster kept the CRD applied on 2026-08-28. The API
server drops an unknown field with nothing but a warning, so `keenDNSZone` had no effect:
the operator still saw `ndns=false` and kept the entries on `domain static` — the reason
the controller sat at zero replicas for six weeks.

When the CRD was updated (2026-10-12), the existing `KeeneticWebApp` objects were patched
to `spec.ndns: true` before scaling back up. The operator computes the same value, but on
startup the WebApp controller could otherwise reconcile an object's old spec before the
Ingress controller rewrote it, and push `domain static` to the router for a few seconds.

`keenetic.hostKey` is deliberately left unset: host-key verification is off, which is
acceptable on this LAN. Pin it with
`ssh-keyscan -t ssh-ed25519 192.168.99.1 | ssh-keygen -lf -` if that stops being true.

## The records point at the router, not at the nginx LB

`192.168.99.1`, not `192.168.99.44`. LAN clients therefore reach an app the same way the
internet does — through the router's KeenDNS reverse proxy — instead of hitting
ingress-nginx directly. The reason is not this operator's: only the router holds a valid
Let's Encrypt certificate for `whitediver.keenetic.link`, and `use-forwarded-headers` on
the controller assumes nothing but the router can reach it. Both are spelled out in
[`../../platform/nginx/README.md`](../../platform/nginx/README.md).

Nothing here configures that address — it comes from `status.loadBalancer` on each
Ingress, which ingress-nginx fills from `--publish-status-address`. Change it there, not
here, or the two will fight.

## `ip host` entries are (name, address) pairs

The router keys a static record on the pair, not on the name:

```console
(config)> ip host probe.invalid 10.99.99.1
(config)> ip host probe.invalid 10.99.99.2
(config)> show running-config
ip host probe.invalid 10.99.99.1
ip host probe.invalid 10.99.99.2
```

Both survive, and the name then resolves round-robin between them. Since
keenetic-operator#21 (image `sha-feb24aa`) `EnsureHost` converges each name onto exactly
one address and deletes every other address it finds for that name, so a changed
`spec.address` cleans up after itself. The flip side: the operator owns the whole name,
not just its own line — a record added by hand for a managed name with a different
address is removed on the next reconcile.

## Blast radius

The operator writes to real router config. It creates one `KeeneticHostRecord` per
(namespace, Ingress host) pair — about 13 in this cluster against the router's hard
64-entry `ip host` limit, so there is headroom, and the chart's `keenetic.maxHosts` guard
refuses to add past 64 rather than letting the router silently drop entries.

Several Ingresses in `mcp` share `dev.whitediver.keenetic.link`. That case needs the
operator's multi-owner handling (a single controller reference would leave all but the
first Ingress failing permanently), so do not roll the image back past the release that
introduced it.

## Monitoring

The manager pod is annotated for Prometheus' `kubernetes-pods` scrape job, so metrics
arrive with no Service and no ServiceMonitor. Beyond the usual `controller_runtime_*`,
it publishes `keenetic_router_hosts` / `keenetic_router_hosts_limit` (headroom against
the cap), `keenetic_router_operations_total` (SSH reachability) and
`keenetic_host_records_limit_rejected_total` — the last one matters because hitting the
cap otherwise returns no error at all and is invisible in `reconcile_errors_total`.
