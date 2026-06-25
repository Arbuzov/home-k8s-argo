# photoprism

PhotoPrism photo library, served at <https://photos.whitediver.keenetic.link/>.
Originals are mounted **read-only** from the cluster's `pictures` SMB share
(`//192.168.99.44/pictures`) — the same share pigallery2/jellyfin read. Pinned
to `kube-worker-3` with a 4Gi memory cap so a heavy index run can't take the
node down. Indexing runs 3 workers (`PHOTOPRISM_WORKERS=3`) across the Pi 5's
4 cores; TensorFlow stays disabled to keep RAM bounded.

## Why the config is tuned the way it is

`kube-worker-3` is a Pi 5 with 8Gi RAM. The 4Gi memory **limit** is the point:
a heavy index run with no cap once OOM-pressured the kubelet into hanging and
took `kube-worker-1` down. With the limit, the pod is OOM-killed at 4Gi instead
of dragging the node with it. 4Gi is enough headroom for the 3 parallel workers
because TensorFlow is disabled (`PHOTOPRISM_DISABLE_TENSORFLOW=true`) — TF is
the big RAM consumer, and dropping it also drops face/classification indexing.
`PHOTOPRISM_WORKERS=3` uses 3 of the 4 cores (one left for kubelet/other pods);
since originals are over SMB, the workers overlap on network I/O wait.

### SQLite over SMB — the `PHOTOPRISM_DATABASE_DSN`

The DSN uses an **absolute** path (`/photoprism/storage/index.db`) so the sqlite
index lands on the persistent `config` volume, not the server's ephemeral CWD
(`/run`, tmpfs). It forces a rollback journal (`_journal=TRUNCATE`): WAL needs an
mmap'd `-shm` file that CIFS can't provide. The `config` PVC is SMB
(`storageClass: smb`), and that mount is `nobrl` (byte-range locking off) with a
single writer — hence `_busy_timeout=30000`, `_txlock=immediate`,
`PHOTOPRISM_DATABASE_CONNS=1`, `..._CONNS_IDLE=0`.

## Ingress: TLS and auth are handled elsewhere

The Keenetic router terminates TLS and forwards plain HTTP to nginx, so nginx's
default HTTP→HTTPS redirect loops forever — `ssl-redirect: "false"` disables it,
same as pigallery2/jellyfin on this cluster.

There is **no** nginx basic-auth in front of PhotoPrism: it does its own login
(Google OIDC + admin-password fallback, `PHOTOPRISM_PUBLIC=false`). The OIDC
callback `/api/v1/oidc/redirect` must be reachable without an outer auth layer,
which a basic-auth annotation would block.

## Originals volume: static SMB-CSI bind

The `originals` mount is a **static** bind, not dynamic provisioning:
`storageClass: "-"` renders `storageClassName: ""`, and the chart-created PVC
`photoprism-originals` is pinned to the `photoprism-pictures` PV by
`volumeName`. That PV (defined at the bottom of `application.yaml`) is an
`smb.csi.k8s.io` volume for `//192.168.99.44/pictures` — the same share
pigallery2 reads — mounted read-only with `uid=gid=999` to match PhotoPrism's
user, and `claimRef` points back at the PVC to reserve the bind. PhotoPrism
never mutates the shared library: it is read-only (`PHOTOPRISM_READONLY=true`)
and its index/thumbnails live in the separate `config` volume.

## Out-of-band Secret: `photoprism-oidc`

Login is **Google OIDC** (`PHOTOPRISM_PUBLIC=false`) using the cluster's
**shared Google OAuth client** — the same client `argo-cd` and `vikunja` use.
The client id/secret plus a generated admin-password fallback live in the
`photoprism-oidc` Secret, created out-of-band (never in git):

```sh
# Reuse the shared Google client (copy from vikunja-oidc) + generate admin pw:
CID=$(kubectl get secret vikunja-oidc -n vikunja -o jsonpath='{.data.google-clientid}' | base64 -d)
CSEC=$(kubectl get secret vikunja-oidc -n vikunja -o jsonpath='{.data.google-clientsecret}' | base64 -d)
kubectl create secret generic photoprism-oidc -n photoprism \
  --from-literal=client-id="$CID" \
  --from-literal=client-secret="$CSEC" \
  --from-literal=admin-password="$(openssl rand -base64 24)"
```

The shared Google OAuth client **must list this redirect URI** (add it in the
Google Cloud Console), or Google rejects login with `redirect_uri_mismatch`:

```
https://photos.whitediver.keenetic.link/api/v1/oidc/redirect
```

Who may log in is governed by that client's Google consent screen (Test users /
allowed domain) — inherited, same as argo-cd/vikunja.

Retrieve the admin-password fallback (login user `admin`):

```sh
kubectl get secret photoprism-oidc -n photoprism -o jsonpath='{.data.admin-password}' | base64 -d; echo
```
