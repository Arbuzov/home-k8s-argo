# keenetic-operator

Syncs cluster Ingress hosts to `ip host` records on the Keenetic router over SSH, so
`*.whitediver.keenetic.link` resolves inside the LAN without anyone editing the router
by hand. Source: [Arbuzov/keenetic-operator](https://github.com/Arbuzov/keenetic-operator),
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
- `nodeSelector` — pinned to `kube-master` like the other singletons. Leader election
  means a second replica would be safe, but only one ever writes to the router, so
  there is nothing to gain from spreading it.

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

## `ip host` entries are (name, address) pairs — changing an address leaves the old one

The router keys a static record on the pair, not on the name:

```console
(config)> ip host probe.invalid 10.99.99.1
(config)> ip host probe.invalid 10.99.99.2
(config)> show running-config
ip host probe.invalid 10.99.99.1
ip host probe.invalid 10.99.99.2
```

Both survive, and the name then resolves round-robin between them. The operator does not
account for this: `EnsureHost` compares the (host, address) pair, sees the new address is
absent and writes it, but never removes the record carrying the *previous* address —
`spec.address` has no `status.appliedAddress` counterpart to clean up from. So a
migration like `192.168.99.44` → `192.168.99.1` needs the stale halves deleted by hand,
once:

```text
no ip host <name> 192.168.99.44     # per name
system configuration save
```

Until that is done, every second lookup lands on the old address — which for this
migration means an intermittent certificate warning rather than a clean failure, so
verify with `show running-config` that exactly one record per name is left.

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
