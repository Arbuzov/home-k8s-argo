# oathkeeper

[Ory Oathkeeper](https://www.ory.sh/oathkeeper/) as a **header-rewriting reverse
proxy** in front of litellm's MCP gateway. Chart `oathkeeper` from
`https://k8s.ory.sh/helm/charts` (308-redirects to `k8s.ory.com`; Helm/Argo
follow it), pinned `0.62.1` (appVersion `v26.2.0`).

Lets a plain MCP client — a Claude custom connector on the existing
`dev.whitediver.keenetic.link/mcp/<server>` URL — reach litellm, which needs an
`x-litellm-api-key` header the connector UI can't set. So tool calls flow through
litellm and land in its per-tool usage stats instead of hitting the MCP pod
directly.

```text
Claude connector (basic-auth in URL)
  -> nginx ingress (validates basic-auth)        [mcp/<server>/application.yaml]
  -> oathkeeper-proxy :4455 (this app, ns mcp)
        anonymous -> allow -> header mutator:
          inject  x-litellm-api-key: Bearer <litellm key>
          overwrite Authorization (drops inbound Basic)
  -> litellm :4000 /mcp/<alias>                   [ai/litellm]
  -> the real MCP server                          [mcp/<server>]
```

Runs in namespace **`mcp`** (not `oathkeeper`) so the mcp-namespace MCP ingresses
can point their backend at the `oathkeeper-proxy` Service — an Ingress can only
reference a Service in its own namespace. [`../project.yaml`](../project.yaml)
whitelists `mcp` as a destination for this reason.

First routed server: **basic-memory** (see the ingress in
[`../../mcp/basic-memory/application.yaml`](../../mcp/basic-memory/application.yaml)).
Add more by pointing each server's ingress backend at `oathkeeper-proxy:4455` and
adding a matching rule to the Secret below.

## Access rules live in a Secret, not git

The rule embeds the litellm key, so it must not be committed. `managedAccessRules:
false` disables the chart's values-rendered rules ConfigMap; instead the pod mounts
the out-of-band Secret **`oathkeeper-rules`** (namespace `mcp`) at
`/etc/secret-rules`, and `access_rules.repositories` points there. **This Secret
must exist before the app syncs** or the pod stays in `CreateContainerConfigError`.

The injected credential is the litellm **master key**, not a virtual key: because
`litellm-postgres` runs on an `emptyDir` (see the litellm README), any Postgres
restart wipes all virtual keys and the injected one dies with it. The master key is
env-sourced (Secret `litellm-masterkey`) and survives. Trade-off: whoever can read
this Secret has litellm admin. **Once litellm-postgres has a PVC, switch to a scoped
virtual key** (`/key/generate`; target servers must be `allow_all_keys: true`, which
`ai/litellm` sets, or the key must be in a team).

Use the `noop` authenticator (not `anonymous`): the nginx ingress already did
basic-auth and forwards an `Authorization` header, which `anonymous` rejects.

```sh
MK=$(kubectl -n litellm get secret litellm-masterkey -o jsonpath='{.data.masterkey}' | base64 -d)

# one rule object per routed MCP server (match /mcp/<litellm-alias>)
cat > access-rules.json <<JSON
[{"id":"mcp-basic-memory",
  "match":{"url":"<http|https>://<[^/]+>/mcp/basic_memory<.*>","methods":["GET","POST","PUT","DELETE","OPTIONS"]},
  "authenticators":[{"handler":"noop"}],
  "authorizer":{"handler":"allow"},
  "mutators":[{"handler":"header","config":{"headers":{
    "x-litellm-api-key":"Bearer $MK","Authorization":"Bearer $MK"}}}],
  "upstream":{"url":"http://litellm.litellm.svc.cluster.local:4000"}}]
JSON
kubectl -n mcp create secret generic oathkeeper-rules --from-file=access-rules.json \
  --dry-run=client -o yaml | kubectl apply -f -
```

Add `jira` / `confluence` / `gitlab` as more rule objects (same shape, alias
swapped) in the same Secret. Oathkeeper hot-reloads the file (kubelet syncs the
Secret to the pod within ~60s).

## Notes

- `serve.proxy.timeout` is raised to 3600s — MCP streamable-http holds long-lived
  responses that Oathkeeper's short default write timeout would truncate.
- Auth is left to the nginx ingress (basic-auth); Oathkeeper only rewrites headers
  (`noop` authenticator). Swap to `jwt` / `oauth2_introspection` here if a
  real OAuth authorization server is ever added (Google OIDC, already used by
  `argo-cd`/`apps/vikunja`, does not support the DCR that Claude connectors want).
