# n8n

Workflow automation, backed by a co-deployed Postgres 15 + Redis. The
chart is `8gears/n8n` from the 8gears OCI registry.

## Required out-of-band secrets

Two Secrets must exist in the `n8n` namespace **before** the Application
syncs (otherwise the pods will fail with missing-env errors):

```sh
# 1. n8n encryption key — used to encrypt credentials at rest.
#    Generate once and never lose it (rotating it invalidates all
#    stored credentials in n8n).
kubectl -n n8n create secret generic n8n-secrets \
  --from-literal=N8N_ENCRYPTION_KEY="$(openssl rand -hex 32)"
# ⚠ MIGRATING an existing install: do NOT generate a fresh key — reuse the
#    one the running pods already use, or every stored credential in the DB
#    becomes undecryptable. See "Migrating to this layout" below.

# 2. Postgres credentials — consumed by both the postgres container
#    (POSTGRES_*) and the n8n main process (DB_POSTGRESDB_PASSWORD).
kubectl -n n8n create secret generic postgres-n8n \
  --from-literal=POSTGRES_DB=n8n \
  --from-literal=POSTGRES_USER=n8n \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 24)"
```

If the namespace doesn't exist yet, run `kubectl create ns n8n` first
(Argo CD will create it on first sync, but the Secrets must precede it,
so do it manually here).

## Notes

- The Postgres `Secret` is **not** rendered by the chart anymore (the old
  inline `extraManifests` Secret was removed to avoid leaking the
  password into git). Postgres still picks up `POSTGRES_DB/USER/PASSWORD`
  via `secretKeyRef`.
- `N8N_ENCRYPTION_KEY` is referenced by all three n8n tiers
  (`main`, `worker`, `webhook`) via `extraEnv.valueFrom.secretKeyRef`.
- `DB_POSTGRESDB_PASSWORD` overrides `db.postgresdb.password` from the
  chart values — n8n picks env vars over file config. The `worker` and
  `webhook` tiers need this env explicitly too (`main` carries its own
  copy); without it they crash with
  `SASL: ... client password must be a string`.
- **Do NOT add `valuesObject` to the `helm:` block.** An empty
  `valuesObject` wins over `values`, and the chart silently renders its
  defaults (no worker/webhook/db tiers).

## Postgres storage (hostPath on kube-master)

Postgres data lives on a **hostPath** — `/var/lib/postgres-n8n-data` on
kube-master, real local POSIX disk — **not** on `smb-pg` and **not** on the
cluster `local-path` class. Both of those are CIFS-backed here (uid forced to
`1000`, chown/chmod denied even as root), and Postgres **corrupts its WAL on
an ungraceful kill over CIFS**: `could not open file pg_wal/...: Invalid
argument` → recovery PANICs → crashloop. Local disk avoids that. The pod is
**pinned to kube-master** (`nodeSelector`) so the hostPath is always the same
physical disk; if the node is lost, rebuild from the nightly `pg_dump`.

The **`smb-pg` StorageClass is still defined in the manifest but is now
unused** (kept for reference / possible reuse). It was the previous attempt:
the underlying `smb-csi` share forces mode `0777` (chmod is a no-op) and
stores files `1000:1000`, while stock Postgres hard-refuses any PGDATA whose
mode isn't `0700`/`0750` with no flag to disable the check. `smb-pg`'s mount
options (`uid=999,gid=999,dir_mode=0700,file_mode=0600`) make the kernel
*present* the share as a `0700` dir Postgres owns, satisfying the check — but
the CIFS WAL corruption above made it unusable for a live DB.

Operational tuning on the Postgres Deployment:

- **Runs as `999:999`** (`runAsUser/Group`, `fsGroup: 999`). hostPath volumes
  are **not** chowned by `fsGroup`, so a root `initContainer` (`init-pgdata`)
  makes PGDATA owned `999:999` / `0700` before Postgres starts (it refuses
  otherwise). The initContainer is idempotent on restart.
- **`Recreate` strategy** so two postmasters never share one PGDATA, plus
  `-c synchronous_commit=off`.
- **Clean fast shutdown:** a `preStop` `pg_ctl ... -m fast` stop, and
  `terminationGracePeriodSeconds: 60` to let it finish before SIGKILL — the
  k8s default is a slow smart shutdown that hits SIGKILL and forces crash
  recovery (the long "not accepting connections") on the next boot.
- **`readinessProbe` is `pg_isready`** so the Service only routes once the DB
  actually accepts connections — n8n/backups don't hit a still-starting
  Postgres.
- **Guaranteed QoS** (`requests == limits`, `512Mi`/`500m`): it uses ~110Mi,
  so it's not OOMKilled at 512Mi and is evicted last under node memory
  pressure.

## Migrating / registering (one-time)

The live `n8n` Argo Application predated this repo: its Helm values were
inline and hand-edited (plaintext secrets). Registering the `ai` app-of-apps
makes git authoritative and flips it in one sync. Do the secret prep first or
you lose DB **access** (the data itself is safe — `Retain` + same subDir —
but auth breaks):

```sh
# 1. Create n8n-secrets with the EXISTING encryption key (NOT a fresh one —
#    a new key makes every stored credential in the DB undecryptable):
kubectl -n n8n create secret generic n8n-secrets \
  --from-literal=N8N_ENCRYPTION_KEY='a_very_long_and_secure_key'

# 2. Make postgres-n8n an out-of-band Secret so prune won't delete it
#    (it holds the right POSTGRES_DB/USER/PASSWORD):
kubectl -n n8n create secret generic postgres-n8n \
  --from-literal=POSTGRES_DB=n8n --from-literal=POSTGRES_USER=n8n \
  --from-literal=POSTGRES_PASSWORD=REDACTED-EXAMPLE-PW

# 3. Register the app-of-apps (one-time, like the apps/mcp/platform roots):
kubectl apply -f ai/bootstrap.yaml
kubectl -n n8n rollout status deploy/postgres-n8n
```

## Backups

Two nightly CronJobs write to the `n8n-backup` PVC (`smb` class, subdir
`pvc-n8n-n8n-backup`), 14-day retention. Both retry/wait because Postgres on
CIFS can be mid-restart. The `n8n-backup` (export) job sets
`enableServiceLinks: false` — its own `N8N_PORT` config clashes with the
legacy service-link env k8s would otherwise inject for the `n8n` Service. The
`n8n-db-backup` job is pinned to kube-master (Postgres + the `postgres:15`
image are local there).

| Job | Time | Tool | Covers | Restore |
|-----|------|------|--------|---------|
| `n8n-backup` | 03:00 | `n8n export:*` | workflows + credentials (**encrypted**), as portable JSON | `n8n import:workflow/credentials` (same encryption key) |
| `n8n-db-backup` | 03:30 | `pg_dump \| gzip` | **the whole DB — everything below** | `gunzip … \| psql` into an empty DB |

**What's in n8n (and which job catches it).** The n8n CLI can export *only*
workflows and credentials — there is **no `export:user`**. Everything else
lives in Postgres and is captured **only by the `pg_dump` job**:

- **Users** (`user`, `user_api_keys`, `user_favorites`) — incl. password hashes
- **Roles / RBAC** (`role`, `role_scope`, `scope`, `role_mapping_rule*`)
- **Projects & membership** (`project`, `project_relation`) and **ownership /
  sharing** (`shared_workflow`, `shared_credentials`)
- **Variables** (`variables`), **Tags** (`tag_entity`)
- **Data Tables** (`data_table*` — e.g. your `claude_state`)
- **Execution history** (`execution_entity`, `execution_data`, annotations)
- **Workflow version history** (`workflow_history`, published versions)
- **Settings** (`settings`), **installed community nodes** (`installed_*`)
- **Webhooks** (`webhook_entity`), **log streaming** (`event_destinations`),
  **SSO/LDAP identities** (`auth_identity`), insights, chat-hub, AI-assistant data

⚠ Whichever you restore, you still need the **same `N8N_ENCRYPTION_KEY`**
(Secret `n8n-secrets`) — credentials are encrypted with it. Back that key up too.

```sh
# Run either backup now (don't wait for the schedule):
kubectl -n n8n create job --from=cronjob/n8n-backup    n8n-backup-now
kubectl -n n8n create job --from=cronjob/n8n-db-backup n8n-db-backup-now

# Portable restore (into a running n8n) — workflows + credentials:
n8n import:workflow --separate --input=/backup/workflows-YYYY-MM-DD.json
n8n import:credentials --input=/backup/credentials-YYYY-MM-DD.json

# Full restore (users + everything) — into an EMPTY database:
gunzip -c /backup/db-YYYY-MM-DD.sql.gz | psql -h postgres-n8n -U n8n -d n8n
```
