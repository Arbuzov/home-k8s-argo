# grafana

Grafana, deployed as an Argo CD `Application` pulling the **native** chart
(`grafana/grafana` from <https://grafana.github.io/helm-charts>) with inline
`helm.values`. Reconciled by the `observability` app-of-apps (`bootstrap.yaml`),
so no manual `kubectl apply`. Runs in `project: default` (destination namespace
`grafana`, which the `observability` AppProject does not whitelist).

## Chart version

Pinned to `10.5.14` (Grafana 12.3.1). Only the very latest `10.5.15` carries
`deprecated: true` upstream and it ships the same app as `10.5.14`, so the
newest *non-deprecated* release is pinned — one below the deprecated tip. Bump
`targetRevision` to move it.

## Dashboards — not provisioned from git

There is **no** dashboard file-provisioning and **no** `gnetId` download. The
dashboards live in Grafana's own database and are edited through the UI or the
Grafana MCP server.

The `dashboardProviders` entry was removed deliberately, not by accident: a file
provider treats git as the source of truth and **deletes** dashboards it does
not know about, which would wipe the set restored into `grafana.db`. Dropping it
also stops the chart from rendering the `download-dashboards` init container,
which used to hang on `curl` to grafana.com and block pod start on this
connection. Don't re-add either without moving the dashboards into git first.

Dashboards still depend on the metrics existing — the sibling `prometheus` app
carries the `argo-cd` scrape job, see
[`../prometheus/README.md`](../prometheus/README.md). Without it the panels
render but read **No data**.

## Storage — database on CNPG Postgres, `grafana.db` gone

Grafana's backing store is the **CloudNativePG** cluster `grafana-pg`
(`grafana-pg-rw:5432`, `ssl_mode: require`), deployed by the sibling
[`application-db.yaml`](application-db.yaml). That Application also runs in
`project: default` for the same reason this one does — ns `grafana` is not in
the `observability` AppProject's `destinations` — and it syncs the CNPG
`Cluster`, its local PV, and the backup `CronJob` from [`db/`](db/).

Postgres rather than SQLite for two reasons: it is consistent with
litellm / n8n / vikunja, and it gives real concurrent access. SQLite kept hitting
`database is locked` — with unified storage and `ngalert` both writing, one
SQLite lock is a single point of contention, and users saw it as `503` on
`/login`. On Postgres those concurrent writers no longer collide, which is why
alerting is left **on** at the chart default.

The password is **not in git**: it comes from the `grafana-pg-app` Secret via
`GF_DATABASE_PASSWORD` (`envValueFrom`), so it never reaches the rendered
`grafana.ini` ConfigMap.

Two different volumes are involved, and it is worth keeping them apart:

- **The database** lives on StorageClass **`grafana-pg-local`** — a static
  `no-provisioner` class defined in [`db/storage.yaml`](db/storage.yaml), with PV
  `grafana-pg-local-1` at `/var/lib/grafana-pg` on **`kube-master`**,
  `Retain`. Same pattern as `litellm-pg-local` and `vikunja-pg-local`, and for
  the same reason: the cluster's own `local-path` class is unusable for this
  (exfat/tmpfs). Its `nodeAffinity` is what pins the CNPG pod to `kube-master`.
- **Grafana's own PVC** is `persistence.existingClaim: grafana-local` — kept for
  plugins and file state, created out-of-band (it is not defined in this repo)
  and pre-seeded from a backup of the old database (13 dashboards /
  3 datasources / 6 alerts). It is node-bound too, so volume affinity pins the
  Grafana pod alongside.

Backups go to a separate PVC on **`smb-pgbackup`**
([`db/backup.yaml`](db/backup.yaml)): a nightly 03:30 `pg_dump` into the shared
`postgres-backups/` tree, same arrangement as `ai/litellm` and `ai/n8n`. That
StorageClass is defined in `ai/n8n` (first mover), so this is a cross-app
dependency — n8n has to be deployed for the backup PVC to bind.

`initChownData` stays **disabled** and `deploymentStrategy: Recreate` stays set:
the volume is RWO, so two pods cannot mount it during a rollout.

> **History.** Originally a hostPath PV (`/srv/kubernetes/grafana` on
> kube-master) → SMB (`smb-grafana`, then the base `smb` class from 2026-06-06)
> → node-local storage + CNPG Postgres. The move off SMB is what removed the
> CIFS locks that made Grafana crash and flicker. Don't move the database back
> onto CIFS.

## Startup probes

The chart at `10.5.14` does **not** render a `startupProbe`, so the whole
slow-start allowance has to live in `livenessProbe`:
`initialDelaySeconds: 180` + `failureThreshold: 20` × `periodSeconds: 15`
≈ 480s. Grafana 12 on ARM needs it — plugin registration plus unified-storage
init runs long, and at the previous ~240s budget liveness killed the pod
mid-start, into a crash loop.

Two settings exist purely to keep that start inside the budget, and both are
about **not reaching grafana.com at boot**:

- `GF_PLUGINS_PREINSTALL_DISABLED: "true"` — stops the bundled drilldown apps
  (`exploretraces`, `lokiexplore`, `pyroscope`, `metricsdrilldown`) from being
  pre-installed. Each one is a ~100s download on this line, and none of the
  dashboards use them.
- `analytics.check_for_plugin_updates: false` — the update check costs ~30s
  *per plugin* here.

If you ever re-enable either, raise the liveness budget in the same commit.

## Access / OAuth

- `https://dev.whitediver.keenetic.link/grafana` (nginx ingress,
  `serve_from_sub_path`, `ssl-redirect: false`).
- Google OAuth with `auto_login`. `allowed_domains` restricts sign-in to
  `whitediver.com`.
- **No `hosted_domain`.** It was briefly set to the Grafana hostname
  (`grafana.whitediver.keenetic.link`), which Grafana passes to Google as the
  `hd=` param — Google then pre-filled that bogus hostname as the `@…` email
  suffix, so the login box demanded an address (`…@grafana.whitediver.keenetic.link`)
  that can't exist. Removed rather than corrected: `allowed_domains` already
  enforces `whitediver.com` server-side, so `hd` adds nothing here, and pointing
  it at `whitediver.com` only works if that's a Google **Workspace** domain
  (it would break login for a personal Google account on the custom domain).
  Don't re-add it.
- `users.auto_assign_org_role: Admin` sets the org role for **new** users at
  creation only — it does **not** re-apply to existing users on later logins.
- `auth.google.skip_org_role_sync: true`: OAuth login must **not** overwrite org
  roles. `role_attribute_path: 'Admin'` does **not** work here — Grafana's INI
  parser strips the wrapping quotes, degrading the JMESPath to a bareword that
  yields nothing, which left existing users stuck as `Viewer`. With sync
  skipped, roles are managed in Grafana directly: `info@whitediver.com` was
  promoted to Admin manually on 2026-06-09; new `whitediver.com` users still get
  Admin via `users.auto_assign_org_role`.

## Secret (out-of-band)

The Google OAuth `client_secret` is **not in git**. It lives in an out-of-band
Kubernetes Secret `grafana-oauth` (ns `grafana`, key `client_secret`) and is
injected into the pod as env `GF_AUTH_GOOGLE_CLIENT_SECRET` via `envValueFrom`
(Grafana maps that env onto `[auth.google] client_secret`, so the value is
absent from the rendered `grafana.ini` ConfigMap). Same pattern as `vikunja-oidc`
— see the publication-prep secrets model. The chart's `assertNoLeakedSecrets`
guard is left at its default (**on**): nothing secret is rendered into the
ConfigMap, so the guard passes and protects against future leaks.

Create the Secret before the change syncs (and on any new cluster) — the pod
stays `CreateContainerConfigError` until it exists. Apply the out-of-band file
(idempotent, re-runnable):

```sh
kubectl --context kubernetes-local apply -f grafana-oauth.secret.yaml
```

> **Cutover ordering (no login break):** apply the Secret **first**, then let
> Argo sync the manifest change. The app-of-apps auto-includes this Application
> (`observability/bootstrap.yaml` glob), so the **git push** is the irreversible
> trigger — the Secret must exist before pushing, not before some manual sync.
> Reusing the existing `client_secret` value keeps all Google logins working —
> only the *location* of the secret moves, not the value. The PVC
> (`persistence.enabled: true`) is never pruned, so `grafana.db` (users,
> dashboards, the manually-set admin password) survives the rollout. Adding the
> env changes the Deployment spec, so the pod **does** roll automatically (unlike
> a pure `grafana.ini` edit, which needs a manual
> `kubectl -n grafana rollout restart deploy/grafana`). If the order is botched
> (manifest synced, Secret missing), recovery is *not* instant — kubelet retries
> on backoff (up to ~5 min); force it with
> `kubectl --context kubernetes-local -n grafana delete pod -l app.kubernetes.io/name=grafana`.

**Note — same value still in `home-k8s-helm`.** This `client_secret` is also
committed in the **private, single-user** `home-k8s-helm` repo (Argo CD dex
config, same Google OAuth client), so moving it out-of-band here doesn't un-leak
it there. That repo is being cleaned separately; given the private scope, rotating
in Google Cloud Console is good hygiene but low-urgency. If you do rotate, update
the `grafana-oauth` Secret **and** the Argo CD `argocd-secret`
(`dex.google.clientSecret`) in lockstep — that one OAuth client backs both Grafana
and Argo CD sign-in.
