# local-cluster-argo

Argo CD `Application` manifests for the home-lab Kubernetes cluster
(`kube-master` + `kube-worker-*`). This is **stage 1** of a split from
[`local-cluster-helm`](https://github.com/Arbuzov/local-cluster-helm) —
only the `Application` resources have been moved here; the local Helm
charts (Grafana, MinIO, MySQL, wg-vless-gateway, wstunnel, …) still
live in the original repo.

## Layout

Top-level directories group services by role:

```
ai/             # AI / automation (n8n)
apps/           # User-facing apps (heimdall, homepage, octoprint, openclaw)
mcp/            # Model Context Protocol servers (atlassian, basic-memory, graphiti, kubernetes, mcpo)
media/          # Media (jellyfin, opds-shelf, photoprism, pigallery2)
networking/     # VPN / tunnels (wg-vless-gateway, wstunnel)
observability/  # Metrics / logs (influxdb, keenetic-grafana-monitoring, prometheus)
platform/       # Cluster plumbing (argo-cd, arc-operator, kubernetes-dashboard, metrics-server)
storage/        # Storage backends (smb)
```

Each service is a directory with:

- `application.yaml` — the Argo CD `Application` resource
- `application-<variant>.yaml` — when one chart is reused for multiple
  Applications (currently only `mcp/atlassian/` for Jira + Confluence)
- `README.md` — present **only** when the service needs out-of-band
  Secrets that you must `kubectl create` before the first sync

## How services are deployed

Two delivery models live side by side:

### `mcp/` — pull-based GitOps (app-of-apps)

The `mcp` app-of-apps ([`mcp/root.yaml`](mcp/root.yaml)) points Argo CD at
this repo on GitHub over HTTPS and reconciles the [`mcp`
AppProject](mcp/project.yaml) plus every MCP `Application` automatically.
Bootstrap it once, after the repo is pushed to GitHub:

```sh
kubectl apply -f mcp/root.yaml
```

From then on Argo CD watches `main`: edit an `mcp/<service>/application.yaml`,
commit and push, and it syncs on its own (the app-of-apps runs `automated`
sync with `prune` + `selfHeal`). The `mcp` children belong to the `mcp`
AppProject; the app-of-apps itself stays in `default` so it can create that
project.

### Everything else — push-based

The other groups are **not** watched by Argo CD. To deliver a change, apply
the Application directly:

```sh
kubectl apply -f <group>/<service>/application.yaml
```

Argo CD then reconciles the inline `helm.values` against the upstream chart
it references.

Two services are different:

- `networking/wg-vless-gateway` — chart still lives at
  `Arbuzov/local-cluster-helm`, path `arbuzov/networking/wg-vless-gateway`
- `networking/wstunnel` — chart still lives at
  `Arbuzov/home-cluster-helm`, path `arbuzov/networking/wstunnel`

For those two, a chart edit requires pushing the *other* repo before
Argo CD picks it up.

## Secrets handling

No credentials are committed to this repo. Everything sensitive
(database passwords, API tokens, OIDC client secrets, …) lives **only**
in Kubernetes `Secret` resources that you create **out-of-band** before
the first sync. Moving to a new cluster means recreating those Secrets
by hand — an accepted trade-off for a home-lab. Each service's
`README.md` has the exact `kubectl create secret` command.

Two wiring styles, both fully declarative from git:

1. **Native Secret reference.** Where a chart can read the value from a
   Secret (`envFrom.secretRef`, `secretKeyRef`, ingress `auth-secret`
   annotations, …) the manifest references it by name; the value never
   appears anywhere but the Secret.

2. **Runtime injection for ConfigMap-rendering charts.** A few charts
   render their config into a `ConfigMap`, which can't hold a Secret
   reference. For those we keep the secret out of **both** git and the
   ConfigMap by injecting it through the app's own env-var mechanism,
   leaving only a harmless placeholder in the committed config:

   | Service         | Secret (namespace)            | Mechanism                                                                      |
   | --------------- | ----------------------------- | ----------------------------------------------------------------------------- |
   | `apps/homepage` | `homepage-secrets` (homepage) | `{{HOMEPAGE_VAR_*}}` in config, resolved at runtime from env                   |
   | `apps/vikunja`  | `vikunja-oidc` (vikunja)      | `VIKUNJA_AUTH_OPENID_PROVIDERS_GOOGLE_CLIENT{ID,SECRET}` env                   |
   | `storage/smb`   | `smbcreds` (smb)              | `${SAMBA_USER}/${SAMBA_PASSWORD}` interpolated by the image at runtime         |
   | `mcp/mcpo`      | `mcpo-secrets` (mcp)          | whole `config.json` from the Secret via chart `existingConfigSecret` (≥ 0.2.7) |

   The committed manifests carry only those placeholders / env
   references, never the real value. (The old `application.local.yaml`
   mechanism is gone; `.gitignore` still excludes `*.local.yaml` as a
   safety net.)

### Secrets each service expects

Create these before applying the corresponding Application (each
service's `README.md` has the concrete command):

| Service                 | Secret(s) (namespace)                                                             |
| ----------------------- | -------------------------------------------------------------------------------- |
| `platform/argo-cd`      | `argocd-secret` (admin bcrypt, Google OIDC client ID/secret), `argocd-redis`     |
| `platform/arc-operator` | `controller-manager` (GitHub PAT)                                                |
| `ai/n8n`                | `n8n-secrets` (encryption key), `postgres-n8n` (DB creds)                        |
| `apps/heimdall`         | `heimdall-postgres` (DB creds)                                                   |
| `apps/homepage`         | `homepage-secrets` (Argo CD homepage token, Home Assistant LLAT)                 |
| `apps/openclaw`         | `openclaw-env-secret`                                                            |
| `apps/vikunja`          | `vikunja-oidc` (Google OIDC client ID/secret — shared with Argo CD)              |
| `mcp/atlassian`         | `mcp-atlassian-{jira,confluence}-credentials`, `mcp-atlassian-vpn-credentials`   |
| `mcp/graphiti`          | `graphiti-neo4j-auth`, `graphiti-mcp-secrets` (Neo4j + OpenAI)                   |
| `mcp/mcpo`              | `mcpo-secrets` (`config.json` incl. Home Assistant LLAT)                         |
| `media/photoprism`      | `photoprism-basic-auth` (htpasswd)                                               |

The remaining services have no secrets in their manifests.

Cluster-wide shared Secrets that several Applications expect to find:

- `smbcreds` in namespace `smb` — one set of samba credentials used by
  the `smb.csi.k8s.io` StorageClasses, by every workload that mounts an
  SMB share, **and** by the samba server itself (`storage/smb` reads it
  via env)
- `mcp-basic-auth` in namespace `mcp` — htpasswd for the `/mcp/*`
  ingress basic-auth annotations

## Bootstrap order

When standing up a fresh cluster:

1. Install Argo CD with `helm` directly (the bootstrap values are still
   in `local-cluster-helm/arbuzov/platform/argo-cd/values.yaml`).
2. Create `argocd-secret` in the `argo-cd` namespace
   (see `platform/argo-cd/README.md`).
3. `kubectl apply -f platform/argo-cd/application.yaml` to hand
   ownership of the install over to Argo CD.
4. Bring up `storage/smb` next — most other workloads mount its shares.
5. Then `platform/metrics-server`, `platform/kubernetes-dashboard`.
6. Apply the rest as needed; create each app's Secrets (per its
   `README.md`) before applying its Application.

## What's intentionally **not** here

- Local Helm charts (`observability/grafana`, `storage/{minio,mysql}`,
  `observability/keenetic-grafana-monitoring`'s wrapper chart,
  `networking/wg-vless-gateway`, `networking/wstunnel`) — they stay in
  `local-cluster-helm`. Two of those (wg-vless-gateway, wstunnel) are
  consumed by Applications **here**.
- Cluster bootstrap (`local-cluster-ansible`).
- One-off scripts (`local-cluster-helm/tools/`).
