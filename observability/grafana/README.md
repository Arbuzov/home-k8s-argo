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

## Storage

Dynamic PVC on the base `smb` StorageClass (`smb.csi.k8s.io`, CIFS). The mount
forces `uid=999,gid=999` (`file_mode=0600`, `nobrl`, `cache=none` — covers
SQLite over CIFS), so:

- `securityContext` runs the pod as **999** (`runAsUser`/`runAsGroup`/`fsGroup`)
  so Grafana can read/write its data dir.
- `initChownData` is **disabled** — chown is a no-op / fails on CIFS (ownership
  is fixed by the mount), so the init step would only error.
- `deploymentStrategy: Recreate` because the volume is **RWO** — two pods can't
  mount it at once during a rollout.

Was on a dedicated `smb-grafana` StorageClass until 2026-06-06, when the base
`smb` class got the same options and the dedicated class was retired to reduce
sprawl. The subDir template `pvc-${ns}-${name}` is identical, so the data folder
on the share is reused as-is.

> Originally migrated from a hostPath PV (`/srv/kubernetes/grafana` on
> kube-master) to SMB; dashboards/datasources from the old deployment were
> **not** carried over (fresh `grafana.db`).

## Startup probes

`livenessProbe`/`readinessProbe` are given long `initialDelaySeconds` (90 / 30)
and high `failureThreshold`. The chart defaults (readiness `initialDelay=0s`,
`timeout=1s`, `failure=3`) kill the pod mid-startup on this Pi cluster — plugin
registration alone can take 50–60s per plugin under CPU/SMB-I/O contention.

## Access / OAuth

- `https://dev.whitediver.keenetic.link/grafana` (nginx ingress,
  `serve_from_sub_path`, `ssl-redirect: false`).
- Google OAuth with `auto_login`. `allowed_domains` restricts sign-in to
  `whitediver.com`.
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
