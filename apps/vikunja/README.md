# vikunja

Self-hosted task manager. Deployed from the official Helm chart
(`ghcr.io/go-vikunja/helm-chart`, **chart** version `v2.0.2` — that's the
chart tag, not the app version; latest as of Jun 2026. Note the upstream
OCI tags are inconsistent: `1.0.0`/`1.1.0`/`2.0.0` have no prefix, but the
newest is `v2.0.2` *with* a leading `v` — `targetRevision` must match the
tag exactly). The `vikunja/vikunja`
image is multi-arch (arm64 included).

Everything in `helm.values` nests under the `vikunja:` key — that's how
this chart is structured.

## Storage

Both volumes use the dynamic `storageClass: smb` (CSI driver
`smb.csi.k8s.io`, backed by the in-cluster samba server at
`192.168.99.44`, see [storage/smb](../../storage/smb/README.md)):

- `database` → `/db`, the SQLite file (`/db/vikunja.db`)
- `data` → `/app/vikunja/files`, task attachments

Because storage is network-backed, the app is **not** pinned to a node
(no `nodeSelector`) — the pod can schedule anywhere.

Two things are required to make this work — both learned the hard way:

1. **Run as uid/gid 999.** The `smb` StorageClass mounts cifs with
   `uid=999,gid=999,dir_mode=0700,file_mode=0600` — cifs ownership is
   fixed by mount options, not the filesystem, so `fsGroup` alone can't
   help. The container's default user is uid 1000, which gets
   `permission denied` on `/db` and `/app/vikunja/files`. Hence the
   `podSecurityContext`/`securityContext` with `runAsUser: 999`,
   `runAsGroup: 999`.

2. **`nobrl` on the `smb` StorageClass.** SQLite takes POSIX byte-range
   (fcntl) locks; over cifs those fail and Vikunja dies at startup with
   `Migration failed: database is locked`. The shared `smb`
   StorageClass was given the `nobrl` mount option (cifs skips
   byte-range lock requests → locking becomes local, fine for a single
   replica). `mountOptions` are immutable, so the class was recreated;
   existing PVs keep their baked-in options, only new PVCs get `nobrl`.
   The `smb` class is a live, hand-managed cluster resource (not in this
   repo). Its current options:
   `vers=3.1.1,serverino,uid=999,gid=999,dir_mode=0700,file_mode=0600,actimeo=1,nobrl`.

> ⚠️ **SQLite on SMB.** Even with `nobrl`, SQLite over a network
> filesystem can still corrupt on network blips. This deployment accepts
> that trade-off to keep storage fully dynamic and the pod un-pinned. If
> you ever see DB corruption, move `database` back to `local-path`
> (node-pinned) or migrate to Postgres on SMB (see `ai/n8n` for the
> Postgres-on-SMB pattern). Keep it to a **single replica** — `nobrl`
> makes locking local, so concurrent writers would corrupt the DB.

## Config

`service.publicurl` is mandatory when CORS is on — without it you get an
`unauthorized` error when creating the first user.

Registration is disabled (`enableregistration: false`). It does not affect
OIDC — signing in with Google provisions the user regardless.

## Branding — custom logo

The header logo is overridden via `service.customlogourl`. Vikunja injects
that value into the served `index.html` as `window.CUSTOM_LOGO_URL` (it is
**not** in `/api/v1/info`) and the frontend uses it as the logo `<img src>`.

The logo source is committed as [`logo.svg`](logo.svg) (a whitediver
dive-mask badge). It is embedded **inline as a `data:` URI** — no external
hosting, ingress path, or CORS needed (the frontend has no CSP that would
block `data:` images). To change the logo, edit `logo.svg` and regenerate
the data URI in `config.yml`:

```sh
python - <<'PY'
import base64, re
uri = "data:image/svg+xml;base64," + base64.b64encode(open('logo.svg','rb').read()).decode()
s = open('application.yaml').read()
open('application.yaml','w').write(re.sub(r'customlogourl: ".*"', f'customlogourl: "{uri}"', s))
PY
```

Then regenerate `application.local.yaml` (Google creds) and re-apply. A
ConfigMap change does **not** auto-restart the pod — `kubectl rollout
restart deploy/vikunja`. (The llama illustration on the login page is a
separate built-in frontend asset and is not affected by `customlogourl`.)

## Auth — Google SSO (OIDC), local login disabled

Login is **Google-only**: `auth.local.enabled: false`, `auth.openid` with
a single Google provider. The web UI shows only "Sign in with Google".

- Reuses the **same Google OAuth client as ArgoCD** (client lives in
  Google Cloud Console; the clientID/secret originate from
  `local-cluster-helm/arbuzov/platform/argo-cd/values.yaml`, Dex config).
  That client must list this redirect URI:
  `https://tasks.whitediver.keenetic.link/auth/openid/google`.
- `authurl` is the **issuer** `https://accounts.google.com` (Vikunja does
  OIDC discovery at `…/.well-known/openid-configuration`) — *not* the full
  authorize endpoint, which would break discovery.
- The provider **map key** (`google`) is what forms the
  `/auth/openid/google` callback path.

> ⚠️ **Config format gotcha.** Modern Vikunja (this is `v2.3.0`) takes
> `auth.openid.providers` as a **map keyed by provider id**, *not* a list.
> The old `providers: - name: Google` array logs `It looks like your
> openid configuration is in the wrong format` and silently yields zero
> providers. Correct shape:
>
> ```yaml
> providers:
>   google:
>     name: "Google"
>     authurl: "https://accounts.google.com"
>     clientid: "…"
>     clientsecret: "…"
>     scope: "openid profile email"
> ```

### Lockout recovery

> ⚠️ **No UI fallback.** With local login off, a broken OIDC config locks
> everyone out of the web UI. To recover, flip `auth.local.enabled: true`
> in `application.local.yaml` and re-apply. User management still works
> out-of-band via the CLI regardless of UI auth:
>
> ```sh
> kubectl exec deploy/vikunja -n vikunja -- ./vikunja user list
> kubectl exec deploy/vikunja -n vikunja -- \
>   ./vikunja user create --email a@b.c --username someone --password '...'
> ```

### Secrets

The Google `clientid`/`clientsecret` come from the pre-existing Secret
`vikunja-oidc` (namespace `vikunja`). This chart renders `config.yml`
into a **ConfigMap** (so the values can't be a plain `secretKeyRef`
inside the file), but Vikunja reads config overrides from environment
variables, so the manifest injects them via `vikunja.env`:

- `VIKUNJA_AUTH_OPENID_PROVIDERS_GOOGLE_CLIENTID` ← key `google-clientid`
- `VIKUNJA_AUTH_OPENID_PROVIDERS_GOOGLE_CLIENTSECRET` ← key `google-clientsecret`

The committed `config.yml` keeps the `google:` provider skeleton (name /
authurl / scope) but **no** `clientid`/`clientsecret` — that provider key
must stay so the env overrides bind onto the providers map (a
Vikunja/viper requirement). The real values live only in the Secret,
never in git or the ConfigMap.

Create the Secret out-of-band:

```sh
kubectl create secret generic vikunja-oidc -n vikunja \
  --from-literal=google-clientid='<google-oauth-client-id>' \
  --from-literal=google-clientsecret='<google-oauth-client-secret>'
```

Then `kubectl apply -f apps/vikunja/application.yaml`. There is no
longer an `application.local.yaml`. To rotate, update the Secret and
`kubectl rollout restart deploy/vikunja -n vikunja`.

> ⚠️ Env-onto-map binding for OIDC providers needs a Vikunja build with
> the Jan-2025 fix (the manifest pins `image.tag: "latest"`, which is
> new enough). After deploy, verify a Google sign-in round-trip works.

## Ingress & public access

Vikunja does **not** behave on a subpath (`/vikunja`) because of the SPA
`base href`, so it gets its own **subdomain** rather than a path (unlike
heimdall/octoprint): `tasks.whitediver.keenetic.link`.

Public access follows the same pattern as every other app here (n8n,
photos, homepage): **TLS is terminated by KeenDNS on the Keenetic
router** — the in-cluster ingress serves plain HTTP on port 80 with no
`tls:` block. Adding a `tls:` block would make nginx 308-redirect
http→https and, since the router proxies to port 80, loop. So the
ingress is HTTP-only by design.

The `nginx.ingress.kubernetes.io/proxy-body-size: "0"` annotation removes
the request-body size limit so attachment uploads aren't capped.

> ⚠️ This chart's ingress key is **`ingressClassName`**, not `className`.
> `className` is silently ignored — the Ingress renders with an empty
> class, nginx never claims it, and every request returns a 404 from the
> default backend even though the pod is healthy.

### Keenetic reverse proxy

The router (`192.168.99.1`, KeeneticOS) maps the KeenDNS name to the
cluster ingress. Configured via the `ip http proxy tasks` preset
(mirrors the `n8n` preset) — proxies to the cluster node on HTTP :80,
preserving the `Host` header so nginx can route by hostname:

```text
ip http proxy tasks
    upstream http d8:3a:dd:27:94:7d 80   # cluster node, nginx ingress :80
    domain ndns                          # binds tasks.<keendns-domain>
    ssl redirect                         # public http→https
    security-level public
    x-real-ip                            # X-Real-IP + X-Forwarded-{For,Port,Proto}
    preserve-host                        # keep Host: tasks.whitediver.keenetic.link
    preserve-referer
    preserve-origin
```

The preset **name** (`tasks`) becomes the subdomain under the registered
KeenDNS domain (`whitediver.keenetic.link`). To recreate: `ssh
admin@192.168.99.1`, create the node with `ip http proxy tasks`, then
set each child with `ip http proxy tasks <param>`, and persist with
`system configuration save`.
