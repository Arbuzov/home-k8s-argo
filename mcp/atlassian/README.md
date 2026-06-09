# mcp-atlassian (Jira + Confluence)

Two sibling Applications:

- `application-jira.yaml`        → Jira MCP at `/mcp/jira`
- `application-confluence.yaml`  → Confluence MCP at `/mcp/confluence`

The Confluence pod also runs an OpenConnect VPN sidecar because the
Confluence instance lives behind a corporate VPN; Jira is reachable
directly and has no sidecar.

## Required out-of-band secrets

Three Secrets in the `mcp` namespace — create before first sync:

```sh
# Jira API token + username (Atlassian PAT, NOT the password)
kubectl -n mcp create secret generic mcp-atlassian-jira-credentials \
  --from-literal=JIRA_USERNAME='<your-atlassian-username>' \
  --from-literal=JIRA_API_TOKEN='<your-atlassian-api-token>'

# Confluence — same shape
kubectl -n mcp create secret generic mcp-atlassian-confluence-credentials \
  --from-literal=CONFLUENCE_USERNAME='<your-atlassian-username>' \
  --from-literal=CONFLUENCE_API_TOKEN='<your-atlassian-api-token>'

# OpenConnect VPN credentials for the Confluence sidecar
kubectl -n mcp create secret generic mcp-atlassian-vpn-credentials \
  --from-literal=USER='<your-vpn-username>' \
  --from-literal=PASS='<your-vpn-password>'
```

The Atlassian Cloud "API token" is generated at
https://id.atlassian.com/manage-profile/security/api-tokens — it is not
the same as your account password.

The pinned server cert (`pin-sha256:…` in `EXTRA_ARGS`) is **not** a
secret — it's a public fingerprint of the corp VPN concentrator's
certificate, used to prevent MITM. It stays inline.

## Basic-auth on the ingress

Both ingresses front the MCP servers with HTTP basic-auth via the
shared `mcp-basic-auth` Secret (htpasswd format) in the `mcp` namespace.
That Secret is also created out-of-band:

```sh
htpasswd -cb auth <user> <password>
kubectl -n mcp create secret generic mcp-basic-auth --from-file=auth
```
