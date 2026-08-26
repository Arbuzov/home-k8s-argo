# mcpo

Open WebUI's MCP aggregator. Fronts several upstream MCP servers (Jira,
Confluence, Home Assistant) behind one ingress at `/mcpo`.

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
`targetRevision: 0.2.8` (`existingConfigSecret` needs ≥ 0.2.7). The `config:`
block in `application.yaml` is kept as **documentation** and as the source for
regenerating the Secret; the chart ignores it while `existingConfigSecret` is
set (the committed `Authorization` value stays a `REPLACE_WITH_*` placeholder).

## No CPU limit — on purpose

`resources` sets `requests.cpu`/`requests.memory` and `limits.memory`, but
**deliberately no `limits.cpu`**. Do not "complete" the block by adding one.

`uvicorn` does not bind `:8000` until its startup hook has *serially* dialled
jira, confluence and home-assistant. Throttled to `300m` on arm64 that takes
~56s, against a liveness budget of 30s + 3 × 10s — so every `kube-master` reboot
cost 10–25 minutes of crash-looping until one attempt happened to win the race.
Uncapped, the startup burst finishes inside the budget. The `requests` still
protect the node's scheduling, which is the part that actually matters here.

## No stdio `memory` server

`config.mcpServers` deliberately omits an stdio `memory` server. The
npx-on-start launch hung ~30s every boot and never connected, losing the
race against the liveness probe (203 restarts). Basic Memory covers the
use case anyway — **don't add it back as a stdio server.**

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
the repo is on GitHub, `kubectl apply -f mcp/bootstrap.yaml` bootstraps the
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
