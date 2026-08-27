# mcp-gateway

A single nginx Ingress that routes MCP servers through
[`../../platform/oathkeeper`](../../platform/oathkeeper) → litellm's MCP gateway,
so tool calls land in litellm's per-tool usage stats.

Those servers set `ingress.enabled: false`; this Ingress owns their public paths.
The paths it actually serves, read off
[`gateway-ingress.yaml`](gateway-ingress.yaml):

| Path (unchanged for clients) | → oathkeeper → litellm |
| --- | --- |
| `/mcp/jira` | `/mcp/jira` |
| `/mcp/confluence` | `/mcp/confluence` |
| `/mcp/kubernetes` | `/mcp/kubernetes` |
| `/mcp/grafana` | `/mcp/grafana` |

**`mcp-gitlab` is not among them**, despite what this file used to say. Its
manifest keeps `ingress.enabled: true` with its own `/mcp/gitlab` path straight
to `mcp-gitlab:3002`, and `gateway-ingress.yaml` has no GitLab entry — so GitLab
traffic bypasses both this Ingress and oathkeeper. That is also why a litellm
outage takes out every other MCP server's public path but leaves `/mcp/gitlab`
working (see [`../../ai/litellm/README.md`](../../ai/litellm/README.md)). To move
it behind the gateway you would have to flip `ingress.enabled: false` in the
GitLab manifest **and** add the path here — and doing that out-of-band is
constrained by the `hostAliases` rule in [`../gitlab/README.md`](../gitlab/README.md).

No rewrite: each path already equals its litellm alias, so it passes straight
through. Same `mcp-basic-auth` as before — Claude connectors are unchanged.

`basic-memory` is **not** here: it keeps its own ingress because it needs a
`/mcp/basic-memory` → `/mcp/basic_memory` rewrite (hyphen→underscore) that this
shared, rewrite-free Ingress can't express.

## gitlab

`gitlab` is deployed **push-based**, out-of-band (excluded from the
app-of-apps), so its live ingress isn't managed here. To route it too: set
`ingress.enabled: false` in the manifest you apply — merged with the live
`hostAliases`, see [`../gitlab/README.md`](../gitlab/README.md) — then add a
`/mcp/gitlab` path to [`gateway-ingress.yaml`](gateway-ingress.yaml). Its litellm
`allow_all_keys` and oathkeeper rule are already in place.

## Cutover note

The chart ingresses have no `prune`, so after `enabled: false` the old live
Ingress objects (`mcp-atlassian-jira`, `mcp-atlassian-confluence`) must be deleted
once by hand — selfHeal will not recreate them.
