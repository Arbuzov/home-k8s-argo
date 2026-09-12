# homepage

`gethomepage/homepage` dashboard. Configuration is inlined via the chart's
`config:` values (services / widgets / bookmarks).

## Version pinning

Two lines carry the version and they are **independent** — bump both or the
bump is a no-op:

- `spec.source.targetRevision` — the UnknownIQ chart version
- `helm.values.image.tag` — the app image

The chart resolves the image as
`{{- $tag := .Values.image.tag | default .Chart.AppVersion -}}`, so an explicit
`tag:` always wins over the chart's `appVersion`. Raising `targetRevision`
alone leaves the pod on the old image while the `app.kubernetes.io/version`
label advertises the new one.

The pin runs deliberately ahead of the chart: chart 1.8.8 was cut hours before
app `v2.3.0` shipped, so its `appVersion` stops one release short for purely
chronological reasons (the same shape as the previous 1.8.4 + `v1.13.2` pair).
Keeping the tag explicit also makes rollback a one-line edit. Do **not** drop
the `tag:` line to inherit `appVersion` — that leaves a bare `image:` key and
the render fails with a nil-pointer (`ComparisonError` in Argo CD); set
`tag: ""` if inheritance is ever actually wanted.

## Service tiles

The `config.services` tiles mirror the cluster's live Ingress hosts/paths
(`kubectl get ingress -A`) — the cluster is the source of truth. Group names
must match `settings.layout` so the column layout applies. Keep tiles plain;
widgets that need an API key/token are provisioned out-of-band via the
`homepage-secrets` Secret (below) — add them per service as the secrets land.

`Multimedia` currently lists PiGallery2 (`photos.whitediver…`, the app
actually deployed on that host) and Calibre-Web; jellyfin + photoprism are
held back via `media/bootstrap.yaml` and not deployed.

Group order in `settings.layout` (News → Developer → System → Home →
Multimedia) and tile order inside each group are click-driven: most-clicked
first, per the `homepage-clicks` Grafana dashboard fed by the tracking below.
The bookmark groups (`News`, `Developer`) are deliberately listed in
`settings.layout` — without an entry there Homepage renders bookmarks at the
bottom of the page, yet they take the majority of clicks. `Multimedia` is
`initiallyCollapsed` (zero clicks so far). Argo CD keeps its high slot
despite few clicks because its value is the glanceable status widget, not
the link itself.

## Tile sizing

Nothing overrides it any more. Homepage's own tile height varies with the
description text, so groups render slightly ragged — that is accepted. The
`customCss` that pinned every tile to a uniform `3.25rem` was removed as unused
in `9ad8783`; do not re-add it on a whim.

If it is ever restored, the hooks it used (`.service-card`,
`li.service:has(.service-container)`, `li.bookmark > a`) still exist verbatim
at app `v2.3.0`, and the chart still mounts `customCss` the same way it mounts
`custom.js`. The one non-obvious rule to carry back: tiles with a widget must
stay `height: auto`, because the widget renders as a second row and a fixed
height clips it away entirely (that is what once hid the Argo CD counters).

Its removal has a consequence for the section below — the CSS used to break
loudly on a markup change, so it doubled as a canary for the click tracker's
selectors. With it gone, the Grafana alert is the **only** backstop.

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

Re-verified against the app `v2.3.0` source on the v1 → v2 bump (2026-09-12):
every hook is still present — `li.service` / `li.bookmark` both carry
`data-name` (`src/components/{services,bookmarks}/item.jsx`), `.service-name`
still holds the name as its first child text node with the description in a
sibling `<p class="service-description">`, and `.services-group` /
`.bookmark-group` still pair with `.service-group-name` /
`.bookmark-group-name` inside the `Disclosure.Button`. A major version is
exactly where this contract would break, so re-run this check on the next one.

Verified live on v2.3.0 too, not just in source: with `navigator.sendBeacon`
stubbed in the browser, a click on the first service tile emitted
`label=Grafana&group=System` and on the first bookmark `label=RBC&group=News` —
no `unknown` / `ungrouped`, and the service label was the name alone rather
than name+description, which confirms the `.service-name` firstChild branch
still applies. All 19 tiles carried `data-name`.

Related objects, provisioned imperatively and **not** in this repo:
InfluxDB database `homepage` (RP `one_year`, 365d), n8n workflow
`Homepage Click Tracker` (`24HSyffBxzdQ9C8S`), Grafana datasource
`influxdb-homepage`, dashboard `homepage-clicks`.

## Secrets and `{{HOMEPAGE_VAR_*}}` substitution

The homepage app substitutes `{{HOMEPAGE_VAR_*}}` placeholders at runtime from
environment variables of the same name, which the manifest injects from two
pre-existing Secrets in namespace `homepage` via `env[].valueFrom.secretKeyRef`.
The real values live **only** in those Secrets — never in git nor in the
rendered ConfigMap.

| Secret | Keys | Feeds |
| --- | --- | --- |
| `homepage-secrets` | `HOMEPAGE_VAR_ARGOCD_TOKEN` | `services[System].Argo CD.widget.key` — Argo CD JWT for the `homepage` apiKey account |
| `homepage-bookmarks` | `HOMEPAGE_VAR_WORK_{GITLAB,JENKINS,JIRA,GRAFANA}_URL` | the four work bookmark `href`s — internal URLs kept out of a public repo |

`homepage-secrets` also still carries `HOMEPAGE_VAR_HA_TOKEN` (a Home Assistant
long-lived access token) from when the Home Assistant tile had a widget. The
manifest no longer references it: the tile is a plain link and there is no
matching `env` entry. Left in place for a future widget — it is inert, not
missing.

> ⚠️ The chart's `env` list only honours `valueFrom` when the entry has
> no `value:` field, so the secret-backed entries deliberately omit it.

## First load after a rollout serves a stale skeleton

Homepage renders the dashboard with `getStaticProps`, so the page in the image
is a **build-time prerender** (`/app/.next/server/pages/en.html`) baked with
upstream's demo config. The real config is picked up by on-demand ISR: the
client calls `/api/revalidate` on mount, Next regenerates the page, and every
later request gets the real one.

That means the very first fetch after a fresh pod — before any browser has
loaded it — returns the skeleton: `My First Group` / `My First Service` /
`Homepage is awesome`, and `__NEXT_DATA__.props.pageProps.initialSettings` is
`{}`. A browser self-heals this within a second of the first visit, so a human
will practically never see it, but **a headless check (`curl`, an uptime probe,
a smoke test) run right after a rollout will, and it looks exactly like a
broken deploy.** It is not.

Forcing it is one request. Since the Google login (below), the public URL
redirects a headless client to Google, so make the request from inside the pod.
The `Host` header must pass `HOMEPAGE_ALLOWED_HOSTS`:
`kubectl -n homepage exec deploy/homepage -- wget -qO- --header 'Host: homepage.whitediver.keenetic.link' http://127.0.0.1:3000/api/revalidate`
→ `{"revalidated":true}`. Do that before concluding a bump broke the config.
Verified on the v2.3.0 bump: pre-revalidation the page was the skeleton;
post-revalidation `initialSettings` carried `theme: dark`, `color: gray`,
`target: _self` and the full layout.

## v2 surfaces deliberately left off

App `v2.x` added two features that stay disabled here, both consciously:

- **Built-in auth.** Driven by env (`HOMEPAGE_AUTH_ENABLED`,
  `HOMEPAGE_AUTH_SECRET` ≥32 chars, `HOMEPAGE_EXTERNAL_URL`, plus password or
  OIDC) — *not* by the chart's `config.auth` / `auth.yaml`, which upstream
  never reads; writing that block only drops an inert file into `/app/config`.
  It stays off because login already happens at the ingress (see *Google
  login* below). Built-in OIDC would also need a new redirect URI on the
  Google client. If auth is ever enabled, note that `custom.js` is **not** on
  the public whitelist (only `/api/healthcheck` and `custom.css` are), so the
  click tracker below would load for signed-in sessions only.
- **`/api/mcp`** (`HOMEPAGE_MCP_ENABLED` + a ≥32-char `HOMEPAGE_MCP_TOKEN`),
  off by default. It must stay off on an internet-reachable ingress: it grants
  read access to every configured service credential and, with writes, control
  of `custom.js`.

Also do **not** add a `persistence` block: the chart's `config-init` skips the
ConfigMap copy whenever `/config/settings.yaml` already exists as a regular
file, so enabling persistence makes GitOps config changes silently stop
applying. And do not add a partial `config.kubernetes` — `mode: cluster` comes
from the chart default via Helm's deep merge, and overriding the key without
re-stating `mode` breaks the Kubernetes widget.

### `HOMEPAGE_ALLOWED_HOSTS` got stricter in v2

The host-validation middleware matcher widened from `/api/:path*` to
essentially every route, so a request whose `Host` is not in the list now
gets a 400 for **the page itself**, not just for widget calls. Ingress traffic
is unaffected, but `kubectl port-forward … 8080:3000` + `http://localhost:8080`
now 400s — verify over `homepage.whitediver.keenetic.link`, or add
`localhost:8080` to the list while debugging.

(The chart injects `config.settings.disableHostValidation: true` into
`settings.yaml`. That key is not upstream vocabulary in any version and does
nothing; the only real escape hatch is `HOMEPAGE_ALLOWED_HOSTS=*`.)

Widget error panels are also terser in v2: `sanitizeErrorURL` now returns only
`<hostname> (see logs for details)`. Status, message and the upstream response
body still show on the tile, but the failing URL does not — use
`kubectl logs deploy/homepage -n homepage`, which still logs it in full.

## Google login

The ingress sits behind the shared Google oauth2-proxy in namespace `mcp`
(`nginx.ingress.kubernetes.io/auth-url` + `auth-signin`). Only the addresses
on that proxy's allow-list get in. The login round-trip goes through
`notes.whitediver.keenetic.link/oauth2/…` and lands back here via an absolute
`rd=`. See [`mcp/oauth2-proxy/README.md`](../../mcp/oauth2-proxy/README.md)
for why one callback host serves every subdomain. The whole page is gated,
`custom.js` included, so the click tracker runs for every visit, since every
visit is signed in. Server-side widget calls (Argo CD, Kubernetes) never pass
through the ingress and are unaffected.

## Concrete steps for this repo

Create the Secrets out-of-band (the keys must match the env-var names):

```sh
kubectl create secret generic homepage-secrets -n homepage   --from-literal=HOMEPAGE_VAR_ARGOCD_TOKEN='<argocd-homepage-token>'   --from-literal=HOMEPAGE_VAR_HA_TOKEN='<home-assistant-llat>'

kubectl create secret generic homepage-bookmarks -n homepage   --from-literal=HOMEPAGE_VAR_WORK_GITLAB_URL='<url>'   --from-literal=HOMEPAGE_VAR_WORK_JENKINS_URL='<url>'   --from-literal=HOMEPAGE_VAR_WORK_JIRA_URL='<url>'   --from-literal=HOMEPAGE_VAR_WORK_GRAFANA_URL='<url>'
```

`homepage` is a pull-based child of the `apps/` app-of-apps (it is **not** in
its `exclude` glob), so once the Secrets exist, commit and push the manifest
and Argo CD syncs it — no manual apply needed. There is no longer an
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
