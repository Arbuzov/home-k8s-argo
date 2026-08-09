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

## Tile sizing

`customCss` (mounted by the chart as ConfigMap `homepage-custom` at
`/app/config/custom.css`, same rollout path as `custom.js`) makes every
service and bookmark tile one uniform, compact size. Homepage's own tile
height varies with the description text, so groups end up ragged; the
override pins `li.service > div` / `li.bookmark > a` to `2.75rem`, shrinks
the name/description type and the icon, and tightens the group margins.

The rem values are the only knobs — tune them there, never the markup: the
selectors deliberately reuse the same semantic hooks (`li.service`,
`li.bookmark`, `.services-group`, `.bookmark-group`) that the click tracker
below depends on, so a Homepage bump breaks (and alerts on) both at once
rather than silently skewing only the layout.

## Link-usage tracking

`customJs` attaches a capture-phase click listener that fires a
`navigator.sendBeacon` at the n8n webhook `homepage-click`, which writes a
point into InfluxDB (`db=homepage`, measurement `homepage_click`, tags
`label` / `group` / `host`). The chart mounts it as ConfigMap
`homepage-custom` at `/app/config/custom.js` and rolls the pod via
`checksum/config`.

Design notes:

- Capture phase + `sendBeacon` are both required: `settings.target: _self`
  navigates away in the same tab, and a plain `fetch` would be cancelled.
- Payload travels as **query params**, not a body — this keeps the request
  CORS-safelisted (no preflight) and lands in n8n's `$json.query`.
- `findLabel` reads the tile's `data-name` attribute, and `findGroup` keys off
  the `.services-group` / `.bookmark-group` container plus its
  `.service-group-name` / `.bookmark-group-name` heading. These semantic
  hooks are part of Homepage's markup contract (they exist so `card-mod`-style
  CSS can target them) and are far more stable than Tailwind utility classes.
  Clicks outside `li.service` / `li.bookmark` — widget links, search results —
  are deliberately not tracked.
- The group heading lives **inside** the `Disclosure.Button`, not as a direct
  child of the group container, so walking ancestors looking for `:scope > h2`
  finds nothing. Use `closest(...)` on the container, then `querySelector` for
  the heading. For nested subgroups this resolves to the innermost subgroup
  name, which is the useful granularity.
- Grafana alert `Homepage: сломались селекторы плиток` (folder `homepage`)
  fires when `label=unknown` or `group=ungrouped` exceeds 3 hits over 6h —
  the signal that a Homepage bump changed the markup contract.

Related objects, provisioned imperatively and **not** in this repo:
InfluxDB database `homepage` (RP `one_year`, 365d), n8n workflow
`Homepage Click Tracker` (`24HSyffBxzdQ9C8S`), Grafana datasource
`influxdb-homepage`, dashboard `homepage-clicks`.

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
