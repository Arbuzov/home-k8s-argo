# homepage

`gethomepage/homepage` dashboard. Configuration is inlined via the chart's
`config:` values (services / widgets / bookmarks).

## Sensitive tokens

Two long-lived tokens drive the Argo CD and Home Assistant widgets:

- `services[Infrastructure].Argo CD.widget.key` — Argo CD JWT for the
  `homepage` apiKey account
- `services[Home assistant].Home assistant.key` — Home Assistant
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

Then apply the Application normally — there is no longer an
`application.local.yaml`:

```sh
kubectl apply -f apps/homepage/application.yaml
```

To rotate a token: update the Secret and restart the pod so homepage
re-reads the env (`kubectl rollout restart deploy/homepage -n homepage`).

## Generating the tokens

```sh
# Argo CD apiKey for the `homepage` account (configured in argo-cd's
# RBAC and accounts.homepage=apiKey)
argocd account generate-token --account homepage

# Home Assistant: Profile → Security → Long-Lived Access Tokens
```
