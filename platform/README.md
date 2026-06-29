# platform

Cluster plumbing. Like `apps/` and `mcp/`, this group is **pull-based
GitOps**: Argo CD watches this repo on GitHub and reconciles the enabled
services under `platform/` automatically.

## Pieces

| File | Kind | Purpose |
| --- | --- | --- |
| [`project.yaml`](project.yaml) | `AppProject` | The `platform` project — restricts sources/destinations for the group |
| [`bootstrap.yaml`](bootstrap.yaml) | `Application` | The **app-of-apps** — deploys `project.yaml` + every enabled child app |
| `<service>/application.yaml` | `Application` | One app each (`project: platform`) |

Services deployed: `argo-cd`, `arc-operator`. Kept in git but **disabled**
via the `exclude` glob in `bootstrap.yaml`: `kubernetes-dashboard`,
`metrics-server`. Each service with out-of-band Secrets documents them in its
own `README.md`.

## How it deploys (app-of-apps)

- [`bootstrap.yaml`](bootstrap.yaml) is the app-of-apps. Its source is this repo on
  GitHub over **SSH** (`git@github.com:Arbuzov/home-k8s-argo.git`,
  branch `main`), path `platform`, with `directory.recurse` + an `include`
  glob that picks up `project.yaml` and every `*/application*.yaml` — but
  **not** `bootstrap.yaml` itself, so the app-of-apps never manages itself.
- It runs `automated` sync with `prune` + `selfHeal`, so committing a change
  under `platform/` and pushing is all it takes to roll out.
- `bootstrap.yaml` lives in the always-present `default` project; the
  [`platform` AppProject](project.yaml) (sync-wave `-1`, created first) governs
  the child Applications, which all carry `project: platform`.

### `argo-cd` is self-managing

The `argo-cd` child Application deploys Argo CD itself. After bootstrap, Argo
CD reconciles its own install from this manifest — so the app-of-apps managing
`argo-cd` means a push to `platform/argo-cd/application.yaml` upgrades the
controller in place. See [`argo-cd/README.md`](argo-cd/README.md) for the
out-of-band `argocd-secret` / `argocd-redis` it expects and the CRD/upgrade
caveats.

## Bootstrap

This group bootstraps **after** Argo CD is already running (Argo CD is
installed first by `helm` directly — see the top-level
[`README.md`](../README.md) bootstrap order). Once the platform changes are
pushed to GitHub, apply the app-of-apps **once**:

```sh
kubectl apply -f platform/bootstrap.yaml
```

From then on Argo CD keeps the `platform` project and all enabled child apps
in sync from git — no further manual `apply` is needed for this group.

Create each service's Secrets (see its `README.md`) before its first sync:

- `platform/argo-cd` — `argocd-secret`, `argocd-redis`
- `platform/arc-operator` — `controller-manager` (GitHub PAT)

## Enabling a disabled service

Remove its path from the app-of-apps `exclude` glob in [`bootstrap.yaml`](bootstrap.yaml):

```yaml
    directory:
      recurse: true
      include: '{project.yaml,*/application*.yaml}'
      exclude: '{metrics-server/application.yaml}'   # e.g. enabling kubernetes-dashboard
```

Then add the service's chart repo to `sourceRepos` and its destination namespace
to `destinations` in [`project.yaml`](project.yaml), or the child Application is
rejected by the `platform` project. The two disabled services need:

| Service | `sourceRepos` entry | `destinations` namespace |
| --- | --- | --- |
| `kubernetes-dashboard` | `https://kubernetes.github.io/dashboard/` | `kubernetes-dashboard` |
| `metrics-server` | `https://kubernetes-sigs.github.io/metrics-server/` | `kube-system` |

Commit + push, then **re-apply the app-of-apps** — `bootstrap.yaml` is excluded
from its own `include` (it never manages itself), so a git push alone does
**not** update the live app-of-apps spec:

```sh
kubectl apply -f platform/bootstrap.yaml
```

Argo CD then adds that `Application`. Disabling works in reverse: add the path
back to `exclude`, push, and re-apply `bootstrap.yaml`; Argo CD prunes the
`Application`.
