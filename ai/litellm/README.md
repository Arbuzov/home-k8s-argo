# litellm

LiteLLM proxy — one OpenAI-compatible endpoint in front of many LLM
backends. Chart is `litellm-helm` from the official `ghcr.io/berriai` OCI
registry. Reachable at `https://litellm.whitediver.keenetic.link`.

## Required out-of-band secrets

Both must exist in the `litellm` namespace **before** the Application
syncs, otherwise the pod stays in `CreateContainerConfigError` /
`CreateContainerError` (the master key is a `secretKeyRef`, the backend
keys an `envFrom.secretRef` — a missing Secret blocks pod start):

```sh
kubectl create ns litellm

# 1. Proxy master key — callers send it as `Authorization: Bearer <key>`;
#    it's also the proxy admin key. Generate once and keep it.
kubectl -n litellm create secret generic litellm-masterkey \
  --from-literal=masterkey="sk-$(openssl rand -hex 24)"

# 2. Backend API keys — one literal per provider env var. The proxy reads
#    them via `os.environ/<KEY>` (see Backends below).
kubectl -n litellm create secret generic litellm-env-secret \
  --from-literal=NVIDIA_NIM_API_KEY="nvapi-..."
```

The chart wires `litellm-masterkey` to `PROXY_MASTER_KEY` (resolved by
`general_settings.master_key: os.environ/PROXY_MASTER_KEY`) and exports
every key in `litellm-env-secret` as a pod env var (`environmentSecrets`).

## DB-less by design

`db.deployStandalone: false` + `db.useExisting: false` +
`migrationJob.enabled: false` — runs as a **stateless gateway/router** off
the config only. No Postgres, no Prisma migration Job. This deliberately
sidesteps the Bitnami-Postgres-on-CIFS corruption trap documented in
[`../n8n/README.md`](../n8n/README.md), and is the minimum that works for
"route requests to backends".

What you give up without a DB: the admin UI, virtual/team keys, budgets,
and request logging. To add them later, point `db.useExisting: true` at a
Postgres (`db.endpoint` + a `db.secret`), flip `migrationJob.enabled: true`,
and switch `image.repository` back to `ghcr.io/berriai/litellm-database`.

## Backends

`proxy_config.model_list` in [`application.yaml`](application.yaml) holds
one **NVIDIA NIM wildcard**:

```yaml
        model_list:
          - model_name: "nvidia_nim/*"
            litellm_params:
              model: "nvidia_nim/*"
              api_key: os.environ/NVIDIA_NIM_API_KEY
```

The `*` passes every NVIDIA NIM model through without enumerating them —
call any of them as `nvidia_nim/<model>` (e.g.
`nvidia_nim/meta/llama-3.3-70b-instruct`). The key comes from
`litellm-env-secret` / `NVIDIA_NIM_API_KEY` (above), never git.

Self-hosted NIM (not the hosted `integrate.api.nvidia.com`)? add
`api_base: <your-nim-url>` under `litellm_params`.

To add another provider: a new `model_list` entry whose `api_key` is
`os.environ/<KEY>`, plus that `<KEY>` added to `litellm-env-secret`
(`environmentSecrets` already exports the whole Secret).

## MCP gateway

`proxy_config.mcp_servers` in [`application.yaml`](application.yaml) registers the
MCP servers from the [`mcp`](../../mcp/) namespace so LiteLLM re-exposes their
tools through its own MCP gateway at `/mcp`:

| Name | In-cluster URL |
| --- | --- |
| `jira` | `http://mcp-atlassian-jira.mcp.svc.cluster.local:8000/mcp/jira` |
| `confluence` | `http://mcp-atlassian-confluence.mcp.svc.cluster.local:8000/mcp/confluence` |
| `basic-memory` | `http://basic-memory.mcp.svc.cluster.local:8000/mcp/basic-memory` |

Cross-namespace, so the `.mcp.svc.cluster.local` FQDN is required. Talking to the
ClusterIP directly bypasses the ingress basic-auth (same as `mcpo` does), and
`transport: http` matches the streamable-http those servers run with. Only the
**enabled** `mcp` apps are listed — `mcpo` is an MCP→OpenAPI proxy (not an MCP
server), and `gitlab`/`graphiti`/`homeassistant`/`kubernetes` are held back in
the app-of-apps `exclude` glob. Add a server here as it comes online.

## Smoke test

```sh
kubectl -n litellm port-forward svc/litellm 4000:4000 &
curl -s localhost:4000/v1/models -H "Authorization: Bearer <masterkey>"
curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer <masterkey>" \
  -d '{"model":"nvidia_nim/meta/llama-3.3-70b-instruct","messages":[{"role":"user","content":"hi"}]}'
```
