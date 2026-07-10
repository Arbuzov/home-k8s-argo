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

# 2. Backend keys + admin-UI Google SSO — one literal per env var. The proxy
#    reads backend keys via `os.environ/<KEY>` (see Backends below); the
#    GOOGLE_* pair drives admin-UI SSO (see "Admin UI & Google SSO").
kubectl -n litellm create secret generic litellm-env-secret \
  --from-literal=NVIDIA_NIM_API_KEY="nvapi-..." \
  --from-literal=NVIDIA_NIM_API_BASE="https://integrate.api.nvidia.com/v1" \
  --from-literal=GOOGLE_CLIENT_ID="...apps.googleusercontent.com" \
  --from-literal=GOOGLE_CLIENT_SECRET="..."
```

The `litellm-db` Secret (Postgres credentials) is **not** out-of-band — it is
committed in [`db/postgres.yaml`](db/postgres.yaml) alongside the Postgres
Deployment.

The chart wires `litellm-masterkey` to `PROXY_MASTER_KEY` (resolved by
`general_settings.master_key: os.environ/PROXY_MASTER_KEY`) and exports
every key in `litellm-env-secret` as a pod env var (`environmentSecrets`).

## DB-backed (Postgres)

`image.repository: ghcr.io/berriai/litellm-database` + `db.useExisting: true`
(pointed at the `litellm-postgres` Deployment in [`db/`](db/)) +
`migrationJob.enabled: true`. The `-database` image variant activates the
DB-backed code paths — admin UI, virtual/team keys, budgets, and request
logging — that `db.useExisting` wires up; a one-shot Prisma migration Job
creates the schema on first sync.

> **History / don't-revert notes**
>
> - litellm previously ran **stateless** (`deployStandalone: false` +
>   `useExisting: false` + `migrationJob.enabled: false`) to sidestep the
>   Bitnami-Postgres-on-CIFS corruption trap (see
>   [`../n8n/README.md`](../n8n/README.md)). It now runs its own plain Postgres
>   on `hostPath` instead (see [Postgres](#postgres-db) below), which avoids
>   that trap while restoring the DB-backed features.
> - Enabling the `-database` image needs real node disk headroom: an earlier
>   retry hit `kube-worker-3` disk contention (~500 MB above the eviction
>   threshold). Freeing it (disabling opds-shelf's 1.3 GB calibre image)
>   brought the margin to ~5.2 GB.
> - `envVars.CHECKPOINT_DISABLE: "1"` (+ `PRISMA_HIDE_UPDATE_MESSAGE`): the
>   Prisma CLI's telemetry/update-check hangs ~60s with no reachable
>   `checkpoint.prisma.io`, which would otherwise eat the whole hardcoded
>   migration timeout. `migrationJob.hooks.{argocd,helm}.enabled: false` — no
>   bundled Postgres to wait on, so sync isn't gated on a hook.

## Admin UI & Google SSO

The admin UI is at `https://litellm.whitediver.keenetic.link` (`PROXY_BASE_URL`).
Google SSO reuses the cluster's shared Google OAuth client (`GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET` in `litellm-env-secret`, the same client as
argo-cd/grafana/vikunja/photoprism); `ALLOWED_EMAIL_DOMAINS: whitediver.com`
gates sign-in and `PROXY_ADMIN_ID` (the owner's Google SSO user id) grants
`proxy_admin`. `UI_USERNAME`/`UI_PASSWORD` still work as a fallback at
`/fallback/login`.

## Request logging is redacted

`litellm_settings.turn_off_message_logging: true` keeps spend/usage rows (tool
name, tokens, cost, key, timestamps — the per-tool stats we want) but redacts
request/response **content**, so corp jira/confluence/gitlab tool payloads never
land in the litellm Postgres.

## Scheduling

`affinity.nodeAffinity` is a **soft** preference for `kube-worker-3`, not a hard
pin: a previous hard pin to one node left litellm stuck `Pending` when that node
had no room. worker-3 has the most spare CPU/RAM — prefer it, don't require it.

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
| `gitlab` | `http://mcp-gitlab.mcp.svc.cluster.local:3002/mcp` |
| `basic_memory` | `http://basic-memory.mcp.svc.cluster.local:8000/mcp/basic-memory` |

`gitlab` differs: port `3002` (its token-injector sidecar adds the `Private-Token`)
and the app serves at `/mcp` (its ingress rewrites `/mcp/gitlab`→`/mcp`), not
`/mcp/gitlab` like the atlassian pair.

Cross-namespace, so the `.mcp.svc.cluster.local` FQDN is required. Talking to the
ClusterIP directly bypasses the ingress basic-auth (same as `mcpo` does), and
`transport: http` matches the streamable-http those servers run with. `gitlab` is
excluded from the `mcp` app-of-apps but is live (applied push-based from its local
overlay — see [`mcp/gitlab/README.md`](../../mcp/gitlab/README.md)), so it's
listed too. Skipped: `mcpo` (an MCP→OpenAPI proxy, not an MCP server) and
`graphiti`/`homeassistant`/`kubernetes` (still held back in the `exclude` glob).
Add a server here as it comes online — note the name key can't contain `-`
(litellm rejects it; use `_`), even though the URL can.

## Postgres (db/)

[`db/postgres.yaml`](db/postgres.yaml) is a plain single-replica `postgres:15`
Deployment + Service + the `litellm-db` Secret, applied by the second source in
[`application.yaml`](application.yaml) (`path: ai/litellm/db`). It is pinned off
`kube-master` onto `kube-worker-3` — keeping node-local `hostPath` storage off
the control-plane node (the single-point-of-failure trap `influxdb` and
`postgres-n8n` are already stuck in); relocating was free while the DB was still
empty (migrations hadn't run).

[`db/nim-sync-cronjob.yaml`](db/nim-sync-cronjob.yaml) syncs the NVIDIA NIM model
catalog into the DB every 3h via `POST /model/new` (ported from the corporate
instance, secret refs adapted to the home split of master key vs provider keys).
It relies on `general_settings.store_model_in_db: true` (set in
[`application.yaml`](application.yaml)) — without it the endpoint 500s with
"Set 'STORE_MODEL_IN_DB=True'".

## Smoke test

```sh
kubectl -n litellm port-forward svc/litellm 4000:4000 &
curl -s localhost:4000/v1/models -H "Authorization: Bearer <masterkey>"
curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer <masterkey>" \
  -d '{"model":"nvidia_nim/meta/llama-3.3-70b-instruct","messages":[{"role":"user","content":"hi"}]}'
```
