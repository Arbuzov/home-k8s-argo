# apps

End-user applications, each in its own namespace. Like `mcp/`, this group is
**pull-based GitOps**: Argo CD watches this repo on GitHub and reconciles
everything under `apps/` automatically.

## Pieces

| File | Kind | Purpose |
| --- | --- | --- |
| [`project.yaml`](project.yaml) | `AppProject` | The `apps` project — restricts sources/destinations for the group |
| [`root.yaml`](root.yaml) | `Application` | The **app-of-apps** — deploys `project.yaml` + every child app |
| `<service>/application.yaml` | `Application` | One app each (`project: apps`) |

Services deployed: `homepage`, `vikunja`, `octoprint`. Kept in git but
**disabled** via the `exclude` glob in `root.yaml`: `heimdall`, `openclaw`.
Each service with out-of-band Secrets documents them in its own `README.md`.

## How it deploys (app-of-apps)

- [`root.yaml`](root.yaml) is the app-of-apps. Its source is this repo on
  GitHub over **HTTPS** (`https://github.com/Arbuzov/local-cluster-argo.git`,
  branch `main`), path `apps`, with `directory.recurse` + an `include` glob
  that picks up `project.yaml` and every `*/application*.yaml` — but **not**
  `root.yaml` itself, so the app-of-apps never manages itself.
- It runs `automated` sync with `prune` + `selfHeal`, so committing a change
  under `apps/` and pushing is all it takes to roll out.
- `root.yaml` lives in the always-present `default` project; the
  [`apps` AppProject](project.yaml) (sync-wave `-1`, created first) governs
  the child Applications, which all carry `project: apps`.

## Bootstrap

Once the repo is pushed to GitHub, apply the app-of-apps **once**:

```sh
kubectl apply -f apps/root.yaml
```

From then on Argo CD keeps the `apps` project and all child apps in sync from
git — no further manual `apply` is needed for this group.

Create each service's Secrets (see its `README.md`) before its first sync.

## Disabling a service (without deleting its manifest)

Add it to the app-of-apps `exclude` glob in [`root.yaml`](root.yaml):

```yaml
    directory:
      recurse: true
      include: '{project.yaml,*/application*.yaml}'
      exclude: '{heimdall/application.yaml,openclaw/application.yaml}'
```

Commit + push, then **re-apply the app-of-apps** — `root.yaml` is excluded
from its own `include` (it never manages itself), so a git push alone does
**not** update the live app-of-apps spec:

```sh
kubectl apply -f apps/root.yaml
```

Argo CD then drops that `Application`. Because every child carries the
`resources-finalizer.argocd.argoproj.io` finalizer, pruning the `Application`
**cascade-deletes its workloads** (Deployments, Services, Ingresses, …) for a
clean teardown. PVCs are retained, so re-enabling — remove the path from
`exclude`, push, and re-apply `root.yaml` — redeploys and re-binds the
existing data.
