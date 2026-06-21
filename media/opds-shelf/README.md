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

## Auth

Three hand-rolled Ingresses replace the chart's single one
(`ingress.enabled: false`), because nginx auth annotations are per-Ingress, not
per-path:

| Ingress | Path(s) | Gate | Why |
| --- | --- | --- | --- |
| `opds-shelf-ui` | `/calibre-web`, `/calibre` | **Google SSO** (oauth2-proxy) | browser UIs — interactive login is fine |
| `opds-shelf-opds` | `/opds` | **HTTP basic-auth** (`opds-shelf-basic-auth`) | e-reader OPDS clients (KOReader, Moon+, …) can't do a Google browser flow |
| `opds-shelf-auth` | `/oauth2` | none | the oauth2-proxy endpoints themselves (`/oauth2/start`, `/callback`, `/auth`) — gating these would loop |

Google SSO is an in-namespace **`oauth2-proxy`** (Deployment + Service): nginx
calls its `/oauth2/auth` (in-cluster) via `auth-url`; on 401 it redirects the
browser to `/oauth2/start` (`auth-signin`) → Google → `/oauth2/callback`. It
reuses the cluster's shared Google OAuth client and restricts logins to
`--email-domain=whitediver.com` (change to an allow-list if you need specific
addresses). `--cookie-secure=false` + an explicit `--redirect-url` because the
Keenetic router terminates TLS and forwards plain HTTP to nginx (same reason
every ingress here sets `ssl-redirect: "false"`).

oauth2-proxy is pinned to `kube-master` (`nodeSelector`) because that is the
only node here that reaches `quay.io` (its CDN times out from the workers);
every other image in this stack is on ghcr.io/docker.io, which the workers can
pull.

## Secrets

Two out-of-band Secrets (nothing sensitive in git, per repo convention):

```sh
# 1. Google OAuth client for oauth2-proxy. Reuse the shared Google client
#    (the one ArgoCD/vikunja/photoprism use) and add this redirect URI to it:
#      https://dev.whitediver.keenetic.link/oauth2/callback
kubectl -n opds-shelf create secret generic opds-shelf-oauth2 \
  --from-literal=client-id='<google-client-id>' \
  --from-literal=client-secret='<google-client-secret>' \
  --from-literal=cookie-secret="$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c 32)"

# 2. htpasswd for the /opds basic-auth gate (e-reader credentials)
kubectl -n opds-shelf create secret generic opds-shelf-basic-auth \
  --from-literal=auth="$(htpasswd -nbB reader '<password>')"
```

It also relies on the cluster-wide `smbcreds` Secret (namespace `smb`) that
every SMB mount already uses.

## Enable

opds-shelf is held back by the `exclude` glob in
[`../bootstrap.yaml`](../bootstrap.yaml). Publish it with `/enable opds-shelf`
(removes it from the glob and flags the one-time `kubectl apply -f
media/bootstrap.yaml`).
