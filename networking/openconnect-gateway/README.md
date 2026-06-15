# openconnect-gateway

Single shared **OpenConnect VPN gateway**. One pod = one session to the corp
concentrator (`vpn.corp.example`); the gitlab and confluence MCP pods route
the corp subnet through it instead of each running their own OpenConnect sidecar.

> **Why:** those sidecars all logged in as the same corp user, and the
> concentrator routes only **one** session per user — two live tunnels →
> **both blackholed**. One shared session removes the conflict.

Like the other `networking/` apps, the chart lives in a separate git repo —
[`Arbuzov/home-cluster-helm`](https://github.com/Arbuzov/home-cluster-helm),
path `arbuzov/networking/openconnect-gateway` (that repo also backs
`wg-vless-gateway`). Edits to the *chart* require a push there before Argo CD
picks them up; edits to this *Application* take effect once it's applied.

## Deploy (push-based, like the rest of `networking/`)

```sh
kubectl apply -f networking/openconnect-gateway/application.yaml
```

Deploys into the **mcp** namespace so it can reuse the existing
`mcp-atlassian-vpn-credentials` Secret and share the namespace (pod-IP routing +
Service DNS) with its client pods.

## Client side

The gitlab and confluence Applications (`mcp/gitlab`, `mcp/atlassian`) no longer
run an OpenConnect sidecar; each runs a small **route-manager** sidecar that
keeps `ip route replace 10.20.0.0/24 via <gateway-pod-ip>` pointed at this
gateway's headless Service. See those manifests and the chart README for detail.

## Rollback

`kubectl delete -f networking/openconnect-gateway/application.yaml` removes the
gateway; `git revert` the gitlab/confluence client edits to restore their own
OpenConnect sidecars.
