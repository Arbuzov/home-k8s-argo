# heimdall

Bookmark / dashboard with a co-located Postgres 15 sidecar.

## Required out-of-band secret

Postgres credentials for the sidecar — create before first sync:

```sh
kubectl -n heimdall create secret generic heimdall-postgres \
  --from-literal=POSTGRES_DB=heimdall \
  --from-literal=POSTGRES_USER=heimdall \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 24)"
```

The sidecar pulls all three keys via `envFrom.secretRef`.
