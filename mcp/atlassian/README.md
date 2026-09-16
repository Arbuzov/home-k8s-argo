# mcp-atlassian (Jira + Confluence)

Two sibling Applications:

- `application-jira.yaml`        → Jira MCP at `/mcp/jira`
- `application-confluence.yaml`  → Confluence MCP at `/mcp/confluence`

Confluence lives behind a corporate VPN, so its pod reaches it through the
shared `openconnect-gateway` (see [`networking/openconnect-gateway/`](../../networking/openconnect-gateway/)),
**not** a per-pod VPN sidecar — see **Confluence corp routing** below. Jira is
reachable directly and needs none of this.

> **Employer-specific values are not in git.** The real Jira/Confluence URLs and
> the VPN subnet live only in the shared out-of-band `mcp-corp-config` Secret
> (`JIRA_URL`/`CONFLUENCE_URL`/`CIDR`); the Atlassian API tokens live in their own
> credential Secrets. The committed manifests carry no corp hostnames.

## Confluence corp routing (shared VPN gateway)

Confluence is only reachable over the corp VPN. Instead of running its own
OpenConnect tunnel, the Confluence pod routes the corp subnet through the shared
**`openconnect-gateway`** pod — one corp session for all clients (per-pod tunnels
all logged in as the same user, and the concentrator routes only one session per
user, so two live tunnels blackholed each other).

- **`route-manager` sidecar:** resolves the gateway's headless Service and keeps
  `ip route replace <corp-subnet> via <gateway-pod-ip>` in place, so the route
  self-repairs if the gateway pod IP changes. The subnet is injected as
  `CORP_CIDR` from the shared `mcp-corp-config` Secret, so it never lands in git.
  Confluence still resolves via cluster DNS; only the L3 path moves to the gateway.
- **`podAffinity` (co-location):** the route's next-hop is the gateway **pod IP**,
  which is only on-link — and thus a usable route — when this pod shares the
  gateway's node. So the pod uses `podAffinity` to follow the gateway onto whichever
  node it lands on (the gateway is label-gated, not pinned to a host). Off-node the
  route fails with `Network unreachable` (and `onlink` doesn't help — ARP for the
  cross-node next-hop never resolves) and corp traffic never reaches the VPN.

VPN credentials, node placement, and gateway internals are documented in
[`networking/openconnect-gateway/README.md`](../../networking/openconnect-gateway/README.md).

## Required out-of-band secrets

Secrets in the `mcp` namespace — create before first sync. The employer-specific
URLs and VPN subnet are kept out of git in one shared, non-credential
`mcp-corp-config` Secret (also read by `mcp/gitlab`); the Atlassian API tokens
live in their own per-service credential Secrets:

```sh
# Shared corp config (URLs + VPN subnet) — NOT credentials, read by jira/confluence/gitlab
kubectl -n mcp create secret generic mcp-corp-config \
  --from-literal=JIRA_URL='https://<your-jira-host>' \
  --from-literal=CONFLUENCE_URL='https://<your-confluence-host>' \
  --from-literal=GITLAB_API_URL='https://<your-gitlab-host>/api/v4' \
  --from-literal=CIDR='<corp-subnet-cidr>'

# Jira API token + username (Atlassian PAT, NOT the password)
kubectl -n mcp create secret generic mcp-atlassian-jira-credentials \
  --from-literal=JIRA_USERNAME='<your-atlassian-username>' \
  --from-literal=JIRA_API_TOKEN='<your-atlassian-api-token>'

# Confluence — same shape
kubectl -n mcp create secret generic mcp-atlassian-confluence-credentials \
  --from-literal=CONFLUENCE_USERNAME='<your-atlassian-username>' \
  --from-literal=CONFLUENCE_API_TOKEN='<your-atlassian-api-token>'

# OpenConnect VPN credentials, used by the shared openconnect-gateway (its
# chart maps USERNAME/PASSWORD onto the USER/PASS env vars the image expects)
kubectl -n mcp create secret generic mcp-atlassian-vpn-credentials \
  --from-literal=USERNAME='<your-vpn-username>' \
  --from-literal=PASSWORD='<your-vpn-password>'
```

The Atlassian Cloud "API token" is generated at
<https://id.atlassian.com/manage-profile/security/api-tokens> — it is not
the same as your account password.

## Routing (ingress disabled — via mcp-gateway)

Both charts set `ingress.enabled: false`; public routing is owned by the shared
[`mcp-gateway`](../mcp-gateway/) Ingress, which forwards `/mcp/jira` and
`/mcp/confluence` through `oathkeeper` → litellm so tool calls land in litellm's
per-tool usage stats (these charts hardcode their own backend and can't be
repointed via values). Each chart's `ingress.path` is **kept** even with the
ingress off: the chart derives the server's `--path` from it
(`deployment.yaml`), and litellm connects to `/mcp/jira` — drop it and the
server falls back to the default `/mcp` and 404s.

Basic-auth is unchanged for clients: the `mcp-gateway` Ingress uses the same
shared `mcp-basic-auth` Secret (htpasswd) in the `mcp` namespace, created
out-of-band:

```sh
htpasswd -cb auth <user> <password>
kubectl -n mcp create secret generic mcp-basic-auth --from-file=auth
```

## `MCP_VERY_VERBOSE: false` — it was the noisiest log on the cluster (2026-09-16)

Both servers ran with `MCP_VERY_VERBOSE: true`, which logs the task scheduler at
DEBUG (`docket.worker - Getting new deliveries` three times a second, forever, whether
or not anyone is using the MCP server).

On kube-worker-3 that was measured at ~1 KiB/s, ~90 MB/day of container log written
straight to the node's SD card — the single largest continuous SD writer left after the
Postgres clusters moved to the SSD (see
[`platform/local-path`](../../platform/local-path/README.md)). Card wear on that node has
already produced six silent-corruption incidents, so pure debug chatter is not worth it.

`MCP_LOGGING_STDOUT` stays on: that is what makes the logs visible to `kubectl logs` at
all. Flip `MCP_VERY_VERBOSE` back to `true` while actually debugging a request, then back.
