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
(pointed at the `litellm-postgres` Deployment in [`db/`](db/)). The `-database`
image variant activates the DB-backed code paths — admin UI, virtual/team keys,
budgets, and request logging — that `db.useExisting` wires up. Schema changes
are applied by hand, not by the chart's Job; see
[Migrations](#migrations-are-manual--the-chart-job-cannot-finish-here) below.

> **History / don't-revert notes**
>
> - litellm previously ran **stateless** (`deployStandalone: false` +
>   `useExisting: false` + `migrationJob.enabled: false`) to sidestep the
>   Bitnami-Postgres-on-CIFS corruption trap (see
>   [`../n8n/README.md`](../n8n/README.md)). It now runs its own plain Postgres
>   on `hostPath` instead (see [Postgres](#postgres--migrated-to-cloudnativepg-2026-07) below), which avoids
>   that trap while restoring the DB-backed features.
> - Enabling the `-database` image needs real node disk headroom: an earlier
>   retry hit `kube-worker-3` disk contention (~500 MB above the eviction
>   threshold). Freeing it (disabling opds-shelf's 1.3 GB calibre image)
>   brought the margin to ~5.2 GB.
> - `envVars.CHECKPOINT_DISABLE: "1"` (+ `PRISMA_HIDE_UPDATE_MESSAGE`): the
>   Prisma CLI's telemetry/update-check hangs ~60s with no reachable
>   `checkpoint.prisma.io`, which would otherwise eat the whole hardcoded
>   migration timeout.

### Migrations are manual — the chart Job cannot finish here

`migrationJob.enabled: false`, and `DISABLE_SCHEMA_UPDATE: "true"` is pinned
**by hand** because of it: the chart only injects that variable while the Job
is enabled, so turning the Job off would otherwise hand schema updates back to
the proxy's own startup path — the one place with even less time budget (its
`startupProbe` already needs the full 15 min documented below).

It goes in `extraEnvVars`, not `envVars`, and that is deliberate. The chart
renders `envVars` (sorted), then `extraEnvVars`, then its own
`DISABLE_SCHEMA_UPDATE` when the Job is enabled. `extraEnvVars` is therefore
the slot that reproduces the exact position the chart's own injection used —
`env` is an ordered list, so putting it anywhere else reorders the container
env, changes the pod template, and forces a rollout. With `strategy.type:
Recreate` and the slow boot below, that is a real outage for a no-op change.
Verified: rendered this way the pod template is byte-identical to the live one,
so disabling the Job restarts nothing.

The Job is off because on this hardware it can never succeed. Measured
2026-08-11 in the running proxy pod (2-CPU limit): a cold
`python -m prisma migrate status` takes **83 s wall / 65 s user** — CPU-bound
single-threaded Node startup, not the network and not Postgres. Every timeout
in `litellm_proxy_extras` is a hardcoded `timeout=60`; there is no env knob.
So every attempt times out, and after its retries the script logs

```text
Database migration failed but continuing startup.
LiteLLM: Setup complete. Skipping server startup as requested.
```

and **exits 0**. The Job reported `Complete` every single time while never
having migrated anything. More CPU does not help — the 83 s figure was already
measured against a 2-core limit, and the work is single-threaded.

To apply a schema change after a version bump, run it by hand with no timeout
(~85 s). **Run it from `litellm_proxy_extras`, not from `/app`** — that is where
the migrations actually live:

```sh
kubectl exec -n litellm deploy/litellm -- sh -c \
  'cd /app/litellm-proxy-extras/litellm_proxy_extras && \
   python -m prisma migrate deploy --schema=schema.prisma'
```

`… migrate status --schema=schema.prisma` is the read-only check — it prints
`141 migrations found` plus either `Database schema is up to date!` or the list
of pending ones (and exits 1 when any are pending).

> **`cd /app` gives a false all-clear — don't use it.** `/app/schema.prisma`
> ships with no `prisma/migrations` directory next to it, so from there the CLI
> prints `No migration found in prisma/migrations` and then
> `Database schema is up to date!` — vacuously true, it compared the DB against
> an empty migration set. Observed 2026-08-13 on the 1.90.0 → 1.96.2 bump: `/app`
> reported the schema clean while the real directory had **14 pending
> migrations** (MCP OAuth client table, `key_type`, savings/compression spend,
> daily tool spend, spend-log index). The proxy runs fine in that state until
> something touches a missing column, so the false all-clear is silent.

#### If the Job is ever re-enabled, keep `hooks.argocd.enabled: true`

The chart default is `true`; it was `false` here on the reasoning that there is
no bundled Postgres to wait on, so a sync need not be gated on a hook. That
reasoning holds, but the side effect does not: with the hook off, the Job is an
ordinary **managed** resource carrying the chart's
`ttlSecondsAfterFinished: 120`. The TTL controller deletes it two minutes after
it completes, `syncPolicy.automated.selfHeal` sees the resource missing and
recreates it, and it runs again — a permanent ~2-minute loop burning Pi CPU and
pinning the Application at `Progressing` (observed 2026-08-11: Job UID
`d99d5fe0` → `a749f829` in 14 minutes).

As a `PreSync` hook the Job leaves the *steady-state* desired set, which is
what breaks the loop: its TTL deletion is no longer drift, so self-heal has
nothing to recreate (verified — the hook Job was TTL-deleted and stayed gone).
It still belongs to Argo during a sync, so a failed hook fails the sync, and
self-heal syncs re-run it. That last part is why the Job is off rather than
merely hooked: a guaranteed-to-time-out migration would block every sync of
this app for ~12 minutes.

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

## The 2026-08-28 disk crunch — how it was resolved

`kube-master` ran out of ephemeral storage during the 2026-08-28 outage and kubelet's image
GC dropped the cached `litellm-database` image. It then could not be re-pulled: the eviction
threshold is `4535063527` bytes (~4.5 GB free) and the node was down to ~3.8 GB, so every
pull attempt was evicted part-way, each leaving more partial content behind. Free space fell
while `containerd` stayed pegged, which starved the API server on the same node. The app was
held at `replicaCount: 0` as a circuit breaker until the node had room.

Room was made by cutting Prometheus retention `90d → 30d` (that TSDB is the largest single
consumer of `/srv/kubernetes/local-provisioner` on the master's card) and clearing exited
containers — 3.8 GB → 6.4 GB free. The arm64 image is 380 MB compressed / ~1.5 GB on disk,
so it now fits with the threshold still satisfied.

**Pre-pull it with `crictl pull` rather than letting the kubelet do it.** A `crictl pull` is
not subject to pod eviction, so it either succeeds or fails cleanly; a kubelet pull that
crosses the threshold mid-way is evicted and leaves partial content, which is what turned a
tight disk into a spiral. Check `df -h /` on the node before and after.

Headroom is thin — roughly 400 MB above the threshold with the image cached. The durable
answer is still moving container storage off the master's SD card
(`roles/master/tasks/usb-mount.yml` in local-cluster-ansible).

## Scheduling

`affinity.nodeAffinity` **requires** litellm off `kube-worker-3` (`NotIn`),
leaving master / worker-1 / worker-2 to choose from.

Why: worker-3 is the Pi 5 running the `rpt-rpi-2712` kernel with a 16K page
size, and this image's CPython 3.13 extensions abort there — `import _socket`
fails with `ValueError: module functions cannot set METH_CLASS or METH_STATIC`,
so the proxy never binds `:4000`. The same image imports fine on the 4K-page
nodes.

The blast radius is wider than litellm: oathkeeper proxies every `/mcp/*` path
to `litellm.litellm.svc:4000`, so a litellm crash-loop means **502 on
basic-memory, confluence, jira, grafana and kubernetes MCP** while all of those
pods sit `Running` and healthy. Only `/mcp/gitlab` survives — it has its own
ingress straight to `mcp-gitlab:3002`, bypassing oathkeeper. Observed
2026-08-10: 34 restarts, whole MCP fleet unreachable from outside.

This used to be a **soft** preference *for* worker-3 (it has the most spare
CPU/RAM) — which is exactly how the pod landed on the one node it cannot run
on. Soft is not enough; under pressure the scheduler still picks it. An earlier
hard pin *to a single node* had left litellm `Pending` when that node had no
room, so don't go back to that either: `NotIn` avoids both traps by excluding
one node rather than demanding one.

The exclusion is by hostname, not by a page-size label — cheapest thing that
works while worker-3 is the only 16K-page node. If a second one appears, label
the nodes (e.g. `pagesize=4k`) and match on that instead.

The schema-migration Job cannot be steered the same way: `litellm-helm` exposes
no `migrationJob.affinity` / `nodeSelector`, so it can still land on worker-3 and
fail there (seen 2026-08-10, `BackoffLimitExceeded`). Re-running it usually lands
elsewhere; the durable fix would be a taint on worker-3, not values.

### CPU limit must allow a burst at boot

`limits.cpu: "2"`, `requests.cpu: 100m`. Idle draw is tens of millicores, but
startup (imports + prisma) is CPU-bound and the 4K-page nodes are Pi 4s. With
the old `500m` cap the container sat pinned at ~445m and never bound `:4000`
inside the chart's `startupProbe` budget (`failureThreshold: 30` ×
`periodSeconds: 10` = 5 min), so the kubelet killed it — *"Container litellm
failed startup probe, will be restarted"*, exit 137, empty logs. That reads like
a crash but is a throttled boot; check `kubectl top pod` against the limit
before blaming the image. CPU requests stay low on purpose — this is burst
headroom, not a reservation.

**Memory requests are the opposite** — `requests.memory: 1Gi`, raised from
`256Mi`. Actual steady-state RSS is ~1073Mi, so the scheduler was
under-accounting this pod roughly 4×: it packed nodes as if litellm needed a
quarter of what it takes, which is how the pod kept landing somewhere it did not
fit. Memory is not compressible, so an under-sized request buys nothing and
risks eviction; keep the request near the real figure.

### The boot is slow because there is only one node it can use

`startupProbe.failureThreshold: 90` (× `periodSeconds: 10` = 15 min) and
`strategy.type: Recreate`.

Lifting the CPU cap was not enough on its own: the pod went from a throttled
445m to 572m and still missed the 5-minute window. Node reality, 2026-08-10 —

| node | CPU | allocatable RAM | usable for litellm |
|---|---|---|---|
| kube-master | 96% | 8 GB | yes — the only one |
| kube-worker-1 | 68% | 829 MiB | no, needs ~1.1 GB |
| kube-worker-2 | — | 829 MiB | no, needs ~1.1 GB |
| kube-worker-3 | 64% | 8 GB free | no, 16K pages (above) |

So litellm has exactly one home, `kube-master`, and that node runs the control
plane plus ~46 pods. Startup is a single thread (`Threads: 1`, RSS ~1 GB, few
syscalls — it is the import phase), so extra cores cannot parallelise it; it
simply needs wall-clock. Idle draw afterwards is tens of millicores.

`Recreate` because a rolling update on a starved single-replica proxy made
things worse: the old crash-looping pod and the new one both burned CPU on the
same node (~240m + ~572m, alongside the 436m migration Job) while neither
became ready. There is no availability to preserve with one replica — take the
brief downtime and let the new pod boot alone.

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

### GitHub Copilot

Copilot models are **not** in `model_list`. They are registered explicitly by
name (no wildcard) into the DB by the sync CronJob — see
[`db/nim-sync-cronjob.yaml`](db/nim-sync-cronjob.yaml) and
[Postgres](#postgres--migrated-to-cloudnativepg-2026-07) below. Auth is the
access-token mounted into the litellm pod, so the CronJob needs no Copilot
credentials of its own.

Two env vars wire that mount up, and the split between them matters:

| Var | Value | Why |
| --- | --- | --- |
| `GITHUB_COPILOT_TOKEN_DIR` | `/tmp/github_copilot` | where litellm **writes** and refreshes the short-lived `api-key.json` — must be writable |
| `GITHUB_COPILOT_ACCESS_TOKEN_FILE` | `/etc/copilot/access-token` | an **absolute** path, so it reads the read-only Secret mount (`litellm-copilot-token`) and ignores `token_dir` |

The list is curated to ids that actually return content on the current
subscription. The full Copilot catalog also exposes gated ids (opus, gpt-5.5)
and ids that answer with an empty response (gemini, kimi) — adding those back
produces models that look registered and then fail at call time.

### Provider egress goes through the in-cluster VLESS gateway

`HTTPS_PROXY: http://wg-vless-gateway-proxy.vpn.svc.cluster.local:1080`, plus a
`NO_PROXY` list.

NVIDIA blocks this ISP's IP range at the edge — **451 before authentication**,
so even an unauthenticated `curl` gets it. Proven from inside this pod: direct
→ 451; through the gateway with the same key → 200 and 102 models.

Two details that are easy to get wrong:

- **`http://`, not `socks5://`.** xray's `:1080` answers HTTP `CONNECT`, and
  this image ships no `socksio`, so a `socks5://` URL raises `ImportError` at
  import time.
- **Only `HTTPS_PROXY`, never `HTTP_PROXY`.** All six `mcp_servers` below are
  plain `http://` cluster URLs and must stay direct. `NO_PROXY` additionally exempts
  cluster DNS suffixes, the Postgres endpoint, and the two Copilot API hosts
  (`api.githubcopilot.com`, `api.github.com`) — Copilot is reachable directly
  and does not need the hop.

The sync CronJob carries the **same** `HTTPS_PROXY` for the same reason. It is
unaffected for its own traffic: it talks to litellm over `http://litellm:4000`,
which `https://`-only proxying leaves direct.

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
| `kubernetes` | `http://mcp-kubernetes.mcp.svc.cluster.local:8080/mcp` |
| `grafana` | `http://grafana-mcp.mcp.svc.cluster.local:8000/mcp` |

`gitlab` differs: port `3002` (its token-injector sidecar adds the `Private-Token`)
and the app serves at `/mcp` (its ingress rewrites `/mcp/gitlab`→`/mcp`), not
`/mcp/gitlab` like the atlassian pair.

Cross-namespace, so the `.mcp.svc.cluster.local` FQDN is required. Talking to the
ClusterIP directly bypasses the ingress basic-auth (same as `mcpo` does), and
`transport: http` matches the streamable-http those servers run with. `gitlab` is
excluded from the `mcp` app-of-apps but is live (applied push-based out-of-band,
merged with the live `hostAliases` — see
[`mcp/gitlab/README.md`](../../mcp/gitlab/README.md)), so it's listed too.
Skipped: `mcpo` (an MCP→OpenAPI proxy, not an MCP server) and
`graphiti`/`homeassistant` (still held back in the `mcp` app-of-apps `exclude`
glob — see [`mcp/README.md`](../../mcp/README.md)).
Add a server here as it comes online — note the name key can't contain `-`
(litellm rejects it; use `_`), even though the URL can.

## Postgres — migrated to CloudNativePG (2026-07)

`db.endpoint` now points at the **CloudNativePG** read-write service
**`litellm-pg-rw`** (`Cluster` `litellm-pg`, operator in `cnpg-system`), not the
old hand-rolled `litellm-postgres` Deployment. `db.useExisting`, the
`litellm-db` Secret (user/password) and `db.database` are unchanged — only the
endpoint moved — so rollback is reverting that one value; the old
`litellm-postgres` Deployment + its `hostPath` data are kept intact until the
CNPG DB is trusted.

Same storage story as n8n (see `ai/n8n/README.md`): CNPG runs Postgres as
**uid 26** and the cluster `local-path` class is unusable (exfat/tmpfs), so the
DB uses a **static `local` PV on the ext4 root disk** — `/var/lib/litellm-pg` on
**kube-worker-3** (where litellm serving + the old DB already live), pre-created
`chown 26:26 chmod 700`, StorageClass `litellm-pg-local` + PV `litellm-pg-local-1`.
`Cluster` `litellm-pg`: 1 instance, image `postgresql:15.18`,
`enableSuperuserAccess: true`. Password reuse: Secret **`litellm-pg-app`**
(`kubernetes.io/basic-auth`, `username=litellm` + the existing `litellm-db`
password) — CNPG adopts the `<cluster>-app` secret so the role keeps the same
password. Out-of-band prereqs (not in git): that secret + the `chown` of the
data dir; CNPG CRDs must already be installed (`platform/cnpg-operator/`).

### Old plain Postgres — RETIRED

The old single-replica `postgres:15` Deployment + Service have been **removed**
from [`db/postgres.yaml`](db/postgres.yaml), which now holds only the still-used
`litellm-db` Secret (litellm reads user/password from it). The old DB ran on a
`hostPath` — `/var/lib/litellm-postgres-data` on **kube-worker-3** — which is
**left on disk** as a rollback safety net (revert the endpoint + re-add the
Deployment to bring it back); reclaim it once the CNPG DB is trusted.
[`db/nim-sync-cronjob.yaml`](db/nim-sync-cronjob.yaml) stays — it talks to
litellm's API, not Postgres.

[`db/nim-sync-cronjob.yaml`](db/nim-sync-cronjob.yaml) syncs the NVIDIA NIM model
catalog into the DB every 3h via `POST /model/new` (ported from the corporate
instance, secret refs adapted to the home split of master key vs provider keys).
It relies on `general_settings.store_model_in_db: true` (set in
[`application.yaml`](application.yaml)) — without it the endpoint 500s with
"Set 'STORE_MODEL_IN_DB=True'".

It also **prunes**: after adding, it `POST /model/delete`s any `nvidia_nim/<id>`
whose id is no longer in the freshly-fetched catalog. NVIDIA drops an end-of-life
model from `/models` (verified: a `410 Gone` model is absent from the catalog) but
litellm keeps the DB registration, so without pruning the council keeps calling a
dead id that 410s — the "models time out / glitch" symptom (a `mistral-large-3`
EOL on 2026-07-23 was the first case). Scope guards: only models with
`litellm_params.model == "nvidia_nim/<id>"` (never the `nvidia_nim/*` wildcard or
`github_copilot/*`), and a `PRUNE_CAP` (15) — if more than that look stale the
catalog probably came back short, so it skips rather than mass-delete good models
(the catalog fetch raises on failure, so a failed fetch aborts before any delete).
The `litellm-council` plugin's `/litellm-council:doctor` command is the client-side
backstop for the same rot.

`ttlSecondsAfterFinished: 86400` on the job template: failed sync jobs linger
(`failedJobsHistoryLimit: 3`) and Argo CD aggregates child-Job health, so a
transient failure (e.g. the 2026-07-16 power-loss/read-only-fs window on
worker-3) kept the `litellm` app **Degraded** long after recovery. The TTL
self-cleans finished jobs after 24h; it only applies to jobs created after the
change — stale failed jobs need one manual
`kubectl -n litellm delete job <litellm-nim-sync-...>`.

> **Deleting the failed Job does not clear `Degraded` — only the next
> successful scheduled run does.** Argo CD 3.4 also health-checks the
> **CronJob itself**: `status.lastScheduleTime` newer than
> `status.lastSuccessfulTime` reads as Degraded, and neither deleting the failed
> Job nor a manual `kubectl create job --from=cronjob/...` touches those fields
> (a manual Job has no owner reference, so it never updates the CronJob status).
> Observed 2026-08-13 during the 1.90.0 → 1.96.2 bump: the 15:00 sync ran into
> the `Recreate` restart window and failed, and the app stayed Degraded through
> `refresh=normal`, `refresh=hard` and a full `argocd-application-controller`
> restart — a fresh controller with a cold cluster cache still reported it. It
> cleared by itself at the 18:00 run. So: check the two CronJob timestamps
> before hunting for a broken workload.

## Backups

[`db/backup.yaml`](db/backup.yaml) — a nightly (03:45) `pg_dump | gzip` CronJob
(`litellm-db-backup`) of the CNPG DB → PVC `litellm-pg-backup` on the shared
`smb-pgbackup` StorageClass, landing in the unified
`smb-csi/postgres-backups/litellm/` (14-day retention, restore with `gunzip … |
psql -h litellm-pg-rw -U litellm -d litellm`). The `smb-pgbackup` class itself
is defined in `ai/n8n/` (first mover) — a cross-app dependency, so this needs
n8n deployed too. Trigger now: `kubectl -n litellm create job
--from=cronjob/litellm-db-backup litellm-db-backup-now`.

## Ingress timeouts — slow reasoning models

The ingress carries two annotations:

```yaml
nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
```

Without them nginx-ingress uses its 60s default `proxy-read-timeout`. The
council (`litellm-council` plugin) sends **non-streaming** requests, so nginx
must hold the connection open until the model finishes the *whole* completion.
Slow reasoning backends (`z-ai/glm-5.2`, `qwen3-next-80b`) on a large diff
routinely run past 60s, and nginx would return **504 Gateway Timeout** — the
"other models periodically 504" symptom. 600s (10 min) is the LLM-gateway norm
and covers the worst case. A per-ingress annotation overrides the controller
default regardless of any global value. (Streaming clients don't hit this — each
chunk resets the read timer — but the council isn't one.)

## Router retries and timeout — the "nemotron times out" report (2026-09-04)

```yaml
router_settings:
  num_retries: 0
  timeout: 280
```

Symptom as reported: `nvidia/nemotron-3-super-120b-a12b` "times out", supposedly
only on long prompts. Measured from inside the `litellm` pod, that diagnosis was
wrong on every count:

| test (through the wg-vless egress) | result |
| --- | --- |
| 90 KB prompt, non-streaming | 200 in 3.0s |
| streamed completion | 200, **392 KB** in 52s |
| 3000-token completion, direct to NIM | 200 in 39s |
| same through the proxy | 200 in 74s |
| 120 KB prompt via the council's own client | 200 in 29s |

So there is no 16 KB volumetric freeze (392 KB crossed the tunnel in one
response) and no 60s wall — the ingress annotations above already handle that,
and the council doesn't even use the ingress, it talks to a `127.0.0.1:4000`
port-forward.

What `LiteLLM_SpendLogs` actually shows for that model: 227 successes, **zero**
failures worth the name, and a duration that tracks `completion_tokens`, not
prompt size — the 897s worst case had a 1.5K-token prompt and a 7.7K-token
completion. Weekly p95/max:

| week | avg completion tok | max | p95 dur | max dur |
| --- | --- | --- | --- | --- |
| 2026-07-27 | 5802 | 27396 | 351s | 485s |
| 2026-08-03 | 7957 | 24172 | 887s | 897s |
| 2026-08-17 | 5832 | 32768 | 288s | 726s |
| 2026-08-24 | 2818 | 9142 | 139s | 275s |
| 2026-08-31 | 2990 | 6738 | **96s** | 180s |

The model is simply a slow reasoning model that ran away to its 32768-token
ceiling, at 9–80 tok/s on the free tier. Nothing timed out server-side; the
*client* gave up at its 300s `AbortSignal`, which is why the proxy logs
successes while the user sees "timeout". The drop after 2026-08-17 is the
`litellm-council` plugin learning to cap `max_tokens` at what its deadline can
pay for — that fix is client-side and already landed.

What was still wrong here: with `num_retries` unset, LiteLLM falls through
`num_retries → litellm.num_retries → openai.DEFAULT_MAX_RETRIES` and lands on
**2**, so a slow call could be attempted three times, and with
`litellm.request_timeout` at its 6000s default nothing bounded any of them. A
call the client abandoned at 300s kept generating for another ten minutes and
could be retried twice more — pure waste of a 40 RPM free tier, and the
mechanism that produced the 897s tail in the first place.

`timeout: 280` sits just under the council's 300s client deadline so the proxy
owns the failure and records it, instead of the client aborting silently while
the server grinds on. `num_retries: 0` because retrying a multi-minute reasoning
completion helps nobody and a 429 retry on the free tier makes things worse.

Both are `router_settings`, read at
`proxy_server.py: router_settings = config.get("router_settings")` and applied
to the Router — the per-model NIM entries live in the DB (written by
`litellm-nim-sync`), so a router-level default is the only place to set this
once for all of them.

## Smoke test

```sh
kubectl -n litellm port-forward svc/litellm 4000:4000 &
curl -s localhost:4000/v1/models -H "Authorization: Bearer <masterkey>"
curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer <masterkey>" \
  -d '{"model":"nvidia_nim/meta/llama-3.3-70b-instruct","messages":[{"role":"user","content":"hi"}]}'
```
