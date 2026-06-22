# opds-shelf

Calibre + Calibre-Web + an OPDS aggregator over a shared book library.
Helm chart `opds-shelf` (`https://arbuzov.github.io/opds-shelf/`), one
Application in the `media` app-of-apps. This README holds the design rationale;
[`application.yaml`](application.yaml) itself is kept comment-free.

## Storage

| Volume | Chart PVC | StorageClass | Backing share | Reclaim | Mounted by |
| --- | --- | --- | --- | --- | --- |
| library `/books` | `opds-shelf-library` (defined here) | static (`""`) | `//192.168.99.44/books` | Retain | calibre (RW), importer (RW), calibre-web (RO) |
| ingest `/ingest` | `opds-shelf-ingest` | `smb` | `//192.168.99.44/smb-csi` (subDir) | Retain | importer |
| calibre `/config` | `calibre-config-opds-shelf-calibre-0` | `smb` | `//192.168.99.44/smb-csi` (subDir) | Retain | calibre |
| calibre-web `/config` | `opds-shelf-calibreweb-config` | `smb` | `//192.168.99.44/smb-csi` (subDir) | Retain | calibre-web |

Everything that is app-owned state (ingest + both `/config` dirs) is on the
cluster **`smb`** StorageClass; the only volume that is *not* on the smb-csi
share is the library, which binds the existing book collection directly.

## Data preservation

- **smb-csi volumes survive a class switch.** The `smb` CSI subDir is
  `pvc-${namespace}-${pvc-name}` — derived from the PVC name, **not** the class
  name (identical template on `smb` and `basic-memory-smb`). So moving the two
  `/config` volumes off `basic-memory-smb` onto `smb` reuses the exact folders a
  prior deploy left on the share (`pvc-opds-shelf-opds-shelf-ingest`,
  `pvc-opds-shelf-opds-shelf-calibreweb-config`,
  `pvc-opds-shelf-calibre-config-opds-shelf-calibre-0`). All are Retain, so the
  data is never deleted.
- **The library is a static bind to `//192.168.99.44/books`** (the `books`
  share served by `storage/smb`, ~2.5 GB of existing books). The
  `opds-shelf-library` PV/PVC at the bottom of the manifest use
  `reclaimPolicy: Retain` and `storageClassName: ""`, so the share content is
  never dynamically provisioned over or deleted — pruning the PVC/PV only
  removes the Kubernetes objects.
- **On disable**, the cascade finalizer prunes the child + the Namespace +
  library PV/PVC; every PV here is Retain/static, so all SMB data survives (same
  model as the rest of `media/` — see [media/README.md](../README.md)).

> Heads-up: the 2.5 GB on the `books` share are loose files, not a Calibre
> library (no `metadata.db`). Calibre-Web shows an empty library until they are
> indexed (`calibredb add`, or drop into `/ingest` for the importer). The files
> are preserved either way — nothing is restructured.

## uid / PUID

The `smb` StorageClass mounts as **uid/gid 999** (CIFS, unix-extensions off, so
the mount uid is forced). The linuxserver calibre/calibre-web containers
therefore run **`PUID/PGID=999`** to keep access to `/config` and the library;
`nobrl` on the mounts lets Calibre's `metadata.db` (SQLite) work over CIFS.

The **importer** overrides the linuxserver entrypoint, so it runs as root and
bypasses the CIFS uid=999 perms (root `CAP_DAC_OVERRIDE`); it is left without an
explicit PUID. `calibredb` then adds `/ingest` books into the library.

## Namespace + library PV/PVC

The Namespace and the `opds-shelf-library` PV/PVC are extra documents in
`application.yaml`, so the **media app-of-apps** (not the child Application)
applies them. The Namespace is declared first so the library PVC has a home the
moment it is applied (Argo orders `Namespace` before `PersistentVolumeClaim`);
the child's `CreateNamespace=true` is a harmless fallback. The chart keeps
`library.persistence.existingClaim: opds-shelf-library`, so it consumes this PVC
instead of provisioning its own.

## Auth & URLs

The chart's single ingress is disabled (`ingress.enabled: false`); routing is
hand-rolled:

| Ingress | URL | Gate |
| --- | --- | --- |
| `opds-shelf-calibreweb` | `books.whitediver.keenetic.link/` | **Google login** (Calibre-Web native OAuth) |
| `opds-shelf-opds` | `dev.whitediver.keenetic.link/opds` | **HTTP basic-auth** (`opds-shelf-basic-auth`) — e-reader clients can't do a Google browser flow |

**Calibre-Web runs at a subdomain root** (`books.whitediver.keenetic.link`),
like photoprism — *not* a subpath (under a subpath it emits root-relative links
that 404 without an `X-Script-Name` header; a root needs no such header for
routing). The KasmVNC **calibre desktop** UI was dropped (same subpath problem);
the `calibre` StatefulSet still runs for `calibredb`/library management.

**Google login is Calibre-Web's built-in OAuth** (flask_dance) — there is no
oauth2-proxy. It's enabled in the `oauthProvider` table of `app.db` (runtime
state on the config PVC, *not* git): the `google` row gets the cluster's shared
Google client id/secret and `active=1`; a pod restart then registers the
`/login/<provider>` blueprint.

> The shared Google OAuth client **must list the redirect URI**
> `https://books.whitediver.keenetic.link/login/google/authorized`, and the
> Keenetic router/DNS must route `books.whitediver.keenetic.link` to the cluster
> (it does — KeenDNS preset `books`).
>
> **https callback:** the router terminates TLS, so nginx sees plain HTTP and
> Calibre-Web — which derives the scheme from the `X-Scheme` header
> (`reverseproxy.py`) — would build an `http://` callback that Google rejects.
> Fix: **`use-forwarded-headers: "true"`** on the ingress-nginx controller
> ConfigMap, so nginx derives `$pass_access_scheme` (→ `X-Scheme`) from the
> router's `X-Forwarded-Proto: https`. (A per-ingress `X-Scheme` snippet does
> **not** work — ingress-nginx overrides `$pass_access_scheme` internally.) That
> controller lives in `local-cluster-helm` — persist the setting there (it's set
> at runtime here).

**Access control / first login:** Calibre-Web has no email-domain filter, so
keep public registration **off** and link accounts explicitly. Log in once
locally (`admin`/`admin123` — change it), open Profile → **Link to Google**;
afterwards "Login with Google" signs that user in. Setting the client id/secret
alone does *not* grant access — an unlinked Google account can't log in unless
registration is open.

## Calibre-Web first run (one-time, on the config PVC — not git)

Calibre-Web's state lives in `app.db` on the `opds-shelf-calibreweb-config`
volume; the library is a Calibre DB (`metadata.db`) at `/books`. Both are
runtime state, not GitOps. On a fresh config volume:

1. Create an empty library so `/books` is a valid Calibre DB (the `books`
   share is a flat dump with no `metadata.db`):
   `kubectl exec -n opds-shelf opds-shelf-calibre-0 -- calibredb list --with-library /books`
2. Point Calibre-Web at it (its DB-config UI is the only in-app way, so set it
   directly):
   `UPDATE settings SET config_calibre_dir='/books'` in `/config/app.db`, then
   restart the calibre-web pod.
3. Import the ~2.5 GB of loose books when ready (drop into `/ingest` for the
   importer, or `calibredb add --with-library /books <files>`).
4. Enable Google login: set the shared Google client id/secret on the `google`
   row of `oauthProvider` and `active=1`, restart the pod (registers
   `/login/google`), then — logged in as `admin` — Profile → **Link to Google**.
   Register `https://books.whitediver.keenetic.link/login/google/authorized` on
   the Google client.

> Do **not** carry a stale `app.db` across a major Calibre-Web version: the
> image is `:latest`, and a schema-incompatible `app.db` makes every request
> 500 (`loadSettings` → `'NoneType' has no attribute 'name'`). If that happens,
> move `app.db` aside and let the pod rebuild it, then redo steps 2–3. Pinning
> the image to a fixed tag avoids the surprise.

## Secrets

One out-of-band Secret — htpasswd for the `/opds` basic-auth gate (e-reader
credentials):

```sh
kubectl -n opds-shelf create secret generic opds-shelf-basic-auth \
  --from-literal=auth="$(htpasswd -nbB reader '<password>')"
```

Google login uses Calibre-Web's **native OAuth**, configured in `app.db` (the
`oauthProvider` table, reusing the cluster's shared Google client id/secret) —
**not** a k8s Secret. Also relies on the cluster-wide `smbcreds` Secret
(namespace `smb`) that every SMB mount already uses.

## Enable

opds-shelf is held back by the `exclude` glob in
[`../bootstrap.yaml`](../bootstrap.yaml). Publish it with `/enable opds-shelf`
(removes it from the glob and flags the one-time `kubectl apply -f
media/bootstrap.yaml`).
