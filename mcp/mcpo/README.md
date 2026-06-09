# mcpo

Open WebUI's MCP aggregator. Fronts several upstream MCP servers (memory,
Jira, Confluence, Home Assistant) behind one ingress at `/mcpo`.

## Sensitive token

`config.mcpServers.home-assistant.headers.Authorization` carries a Home
Assistant Long-Lived Access Token as a Bearer header. mcpo reads its
whole config from `config.json` and does **not** expand env vars inside
it, so the token can't be injected as an env var. Instead the mcpo chart
(>= 0.2.7) supports **`existingConfigSecret`**: the entire `config.json`
— including the real token — is provided by a pre-existing Secret, and
the chart skips the ConfigMap entirely. The token therefore never lands
in git nor in a plaintext ConfigMap.

The manifest sets `existingConfigSecret: mcpo-secrets` and pins chart
`targetRevision: 0.2.7`. The `config:` block in `application.yaml` is
kept as **documentation** and as the source for regenerating the Secret;
the chart ignores it while `existingConfigSecret` is set (the committed
`Authorization` value stays a `REPLACE_WITH_*` placeholder).

## Concrete steps for this repo

Build the real `config.json` from the committed `config:` block with the
token substituted in, then create the Secret out-of-band:

```sh
# config.json mirrors spec.source.helm.values.config (mcpServers: ...) with
# the real "Bearer <HA-LLAT>" in home-assistant.headers.Authorization.
kubectl create secret generic mcpo-secrets -n mcp \
  --from-file=config.json=./config.json
```

mcpo is deployed through the **`mcp` app-of-apps** (it belongs to the
`mcp` AppProject) — there is no longer an `application.local.yaml`. Once
the repo is on GitHub, `kubectl apply -f mcp/root.yaml` bootstraps the
whole `mcp` group and Argo CD keeps it synced. For a one-off direct apply
you must create the project first (it carries `project: mcp`):

```sh
kubectl apply -f mcp/project.yaml
kubectl apply -f mcp/mcpo/application.yaml
```

To rotate the LLAT (HA -> Profile -> Security -> Long-Lived Access
Tokens): rebuild `config.json`, update the Secret, and
`kubectl rollout restart deploy/mcpo -n mcp` (config is read only at
container start).
