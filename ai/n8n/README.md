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
  chart values — n8n picks env vars over file config.

## Postgres storage (smb-pg PVC, pinned to kube-master)

Postgres data lives on the `smb-csi` share via the dedicated **`smb-pg`**
StorageClass (PVC `postgres-n8n-pg`, subDir `pvc-n8n-postgres-n8n-pg`).

It does **not** use a plain `hostPath` to the underlying directory, even
though kube-master is the Samba server. That path is a **CIFS mount that
forces mode `0777`** (chmod is a no-op, even for the owner) and stores files
as `1000:1000`. Stock Postgres hard-refuses any PGDATA whose mode isn't
`0700`/`0750` and there's no flag to disable that check — so hostPath fails
with *"data directory has invalid permissions"*. The `smb-pg` mount options
(`uid=999,gid=999,dir_mode=0700,file_mode=0600`) make the kernel **present**
the share to Postgres as a `0700` dir it owns, which is the only thing that
satisfies the check. Hence Postgres runs as `runAsUser/Group/fsGroup: 999`.

Kept from the operational tuning: the pod is **pinned to kube-master** (the
smb server) and uses a **Recreate** strategy so two postmasters never share
one PGDATA, plus `-c synchronous_commit=off`.

`reclaimPolicy: Retain` + the deterministic subDir mean the data survives a
PVC delete/recreate: a re-provisioned `postgres-n8n-pg` remaps to the same
share folder.

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
kubectl apply -f ai/root.yaml
kubectl -n n8n rollout status deploy/postgres-n8n
```

## Backups

Two nightly CronJobs write to the `n8n-backup` PVC (`smb` class, subdir
`pvc-n8n-n8n-backup`), 14-day retention. Both retry/wait because Postgres on
CIFS can be mid-restart.

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
