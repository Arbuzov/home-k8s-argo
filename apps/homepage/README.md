# homepage

`gethomepage/homepage` dashboard. Configuration is inlined via the chart's
`config:` values (services / widgets / bookmarks).

## Service tiles

The `config.services` tiles mirror the cluster's live Ingress hosts/paths
(`kubectl get ingress -A`) — the cluster is the source of truth. Group names
must match `settings.layout` so the column layout applies. Keep tiles plain;
widgets that need an API key/token are provisioned out-of-band via the
`homepage-secrets` Secret (below) — add them per service as the secrets land.

`Multimedia` currently lists PiGallery2 (`photos.whitediver…`, the app
actually deployed on that host) and Calibre-Web; jellyfin + photoprism are
held back via `media/bootstrap.yaml` and not deployed.

## Sensitive tokens

Two long-lived tokens drive the Argo CD and Home Assistant widgets:

- `services[System].Argo CD.widget.key` — Argo CD JWT for the
  `homepage` apiKey account
- `services[Home].Home assistant.key` — Home Assistant
  long-lived access token

The committed config carries them as `{{HOMEPAGE_VAR_ARGOCD_TOKEN}}` and
`{{HOMEPAGE_VAR_HA_TOKEN}}` placeholders. The homepage app substitutes
`{{HOMEPAGE_VAR_*}}` tokens at runtime from environment variables of the
same name, which the manifest injects from the pre-existing Secret
`homepage-secrets` (namespace `homepage`) via
`env[].valueFrom.secretKeyRef`. The real tokens live **only** in that
Secret — never in git nor in the rendered ConfigMap.

> ⚠️ The chart's `env` list only honours `valueFrom` when the entry has
> no `value:` field, so the secret-backed entries deliberately omit it.

## Concrete steps for this repo

Create the Secret out-of-band (the keys must match the env-var names):

```sh
kubectl create secret generic homepage-secrets -n homepage \
  --from-literal=HOMEPAGE_VAR_ARGOCD_TOKEN='<argocd-homepage-token>' \
  --from-literal=HOMEPAGE_VAR_HA_TOKEN='<home-assistant-llat>'
```

`homepage` is a pull-based child of the `apps/` app-of-apps (it is **not** in
its `exclude` glob), so once `homepage-secrets` exists, commit and push the
manifest and Argo CD syncs it — no manual apply needed. There is no longer an
`application.local.yaml`. (For a one-off direct apply:
`kubectl apply -f apps/homepage/application.yaml`.)

To rotate a token: update the Secret and restart the pod so homepage
re-reads the env (`kubectl rollout restart deploy/homepage -n homepage`).

## Generating the tokens

```sh
# Argo CD apiKey for the `homepage` account (configured in argo-cd's
# RBAC and accounts.homepage=apiKey)
argocd account generate-token --account homepage

# Home Assistant: Profile → Security → Long-Lived Access Tokens
```
