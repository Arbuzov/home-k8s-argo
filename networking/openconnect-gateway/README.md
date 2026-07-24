# openconnect-gateway

Single shared **OpenConnect VPN gateway**. One pod = one session to the corporate
VPN concentrator; the gitlab and confluence MCP pods route the corp subnet through
it instead of each running their own OpenConnect sidecar.

> **Why:** those sidecars all logged in as the same corp user, and the
> concentrator routes only **one** session per user — two live tunnels →
> **both blackholed**. One shared session removes the conflict.
>
> **Employer-specific values are not in git.** The concentrator hostname, tunnel
> group, and corp subnet live only in the (private) chart and in out-of-band
> Secrets, never in this repo.

The chart lives in a **private** Helm repo at
`arbuzov/networking/openconnect-gateway`, like the other `networking/` apps
(`wstunnel`, `wg-vless-gateway`). Argo CD pulls it via the
`repo-home-k8s-helm` credential. Edit the chart there and push; Argo CD picks
it up on the next sync. Only `application.yaml` lives here.

## Deploy (push-based, like the rest of `networking/`)

```sh
kubectl apply -f networking/openconnect-gateway/application.yaml
```

The Application lives in the **`argo-cd`** namespace — where this cluster's Argo CD
watches for Applications (alongside the `mcp`/`platform` apps); the older
`networking/` apps reference `argocd`, which does not exist here.

Deploys into the **mcp** namespace so it can reuse the existing
`mcp-atlassian-vpn-credentials` Secret and share the namespace (pod-IP routing +
Service DNS) with its client pods.

## Client side

The gitlab and confluence Applications (`mcp/gitlab`, `mcp/atlassian`) no longer
run an OpenConnect sidecar; each runs a small **route-manager** sidecar that
keeps `ip route replace <corp-subnet> via <gateway-pod-ip>` pointed at this
gateway's headless Service. See those manifests for detail.

## Liveness / tunnel health

The pod's liveness probe checks that the tunnel actually **carries traffic**, not
just that the `tun127` interface exists. When the corporate concentrator drops
the session (ASA idle-timeout), openconnect loops on in-place reconnect
(`vpnc-script returned error 1`) while `tun127` keeps its inet address — so an
interface-only probe stays green and the pod is never restarted, and all corp
traffic (gitlab, confluence, jira) blackholes until a manual bounce. This showed
up as dozens of gateway restarts and the gitlab MCP `tools/call` hanging to
timeout while `initialize` still returned instantly.

The probe therefore also runs `nc -w3 <host> <port>` **through** the tunnel; a
failed connect restarts the pod, and a fresh openconnect session reconnects
cleanly (~90s worst case: `periodSeconds` 30 × `failureThreshold` 3).

Configured in the private chart's `values.yaml`:

- `healthcheck.host` — a stable corp canary (use the DNS/DC, **not** a single
  app, so app downtime doesn't thrash the VPN). Empty ⇒ interface-only check
  (previous behavior), keeping the chart topology-free by default.
- `healthcheck.port` — TCP port to connect to (e.g. `53` for the DC/DNS).

## Rollback

`kubectl delete -f networking/openconnect-gateway/application.yaml` removes the
gateway; `git revert` the gitlab/confluence client edits to restore their own
OpenConnect sidecars.
