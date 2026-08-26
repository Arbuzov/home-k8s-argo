# mcp-gateway

A single nginx Ingress that routes MCP servers through
[`../../platform/oathkeeper`](../../platform/oathkeeper) → litellm's MCP gateway
(so tool calls land in litellm's per-tool usage stats), for the servers whose
chart **hardcodes its own ingress backend** and therefore can't be repointed via
Helm values: `mcp-atlassian` (jira, confluence) and `mcp-gitlab`.

Those charts have `ingress.enabled: false`; this Ingress owns their public paths:

| Path (unchanged for clients) | → oathkeeper → litellm |
| --- | --- |
| `/mcp/jira` | `/mcp/jira` |
| `/mcp/confluence` | `/mcp/confluence` |

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
