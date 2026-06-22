# photoprism

PhotoPrism photo library, served at <https://photos.whitediver.keenetic.link/>.
Originals are mounted **read-only** from the cluster's `pictures` SMB share
(`//192.168.99.44/pictures`) — the same share pigallery2/jellyfin read. Pinned
to `kube-worker-3` with a 4Gi memory cap so a heavy index run can't take the
node down. Indexing runs 3 workers (`PHOTOPRISM_WORKERS=3`) across the Pi 5's
4 cores; TensorFlow stays disabled to keep RAM bounded.

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
