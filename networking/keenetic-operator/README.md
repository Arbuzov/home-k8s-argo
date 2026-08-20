# keenetic-operator

Syncs cluster Ingress hosts to `ip host` records on the Keenetic router over SSH, so
`*.whitediver.keenetic.link` resolves to the nginx LB from inside the LAN without anyone
editing the router by hand. Source: [Arbuzov/keenetic-operator](https://github.com/Arbuzov/keenetic-operator),
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
- `keenetic.defaultIngressIP` — `192.168.99.44`, the shared nginx LB. Only a fallback:
  every Ingress in this cluster already reports that address in
  `status.loadBalancer`, so the operator normally reads it from there.
- `nodeSelector` — pinned to `kube-master` like the other singletons. Leader election
  means a second replica would be safe, but only one ever writes to the router, so
  there is nothing to gain from spreading it.

`keenetic.hostKey` is deliberately left unset: host-key verification is off, which is
acceptable on this LAN. Pin it with
`ssh-keyscan -t ssh-ed25519 192.168.99.1 | ssh-keygen -lf -` if that stops being true.

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
