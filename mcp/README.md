# mcp

Model Context Protocol servers, deployed to the `mcp` namespace. Unlike the
other groups (which are applied push-based with `kubectl apply`), this group
is **pull-based GitOps**: Argo CD watches this repo on GitHub and reconciles
everything under `mcp/` automatically.

## Pieces

| File | Kind | Purpose |
| --- | --- | --- |
| [`project.yaml`](project.yaml) | `AppProject` | The `mcp` project — restricts sources/destinations for the group |
| [`root.yaml`](root.yaml) | `Application` | The **app-of-apps** — deploys `project.yaml` + every child app |
| `<service>/application*.yaml` | `Application` | One MCP server each (`project: mcp`) |

Services: `atlassian` (Jira + Confluence), `basic-memory`, `graphiti`,
`homeassistant`, `kubernetes`, `mcpo`. Each has its own `README.md` for the
out-of-band Secrets it expects.

## How it deploys (app-of-apps)

- [`root.yaml`](root.yaml) is the app-of-apps. Its source is this repo on
  GitHub over **HTTPS** (`https://github.com/Arbuzov/local-cluster-argo.git`,
  branch `main`), path `mcp`, with `directory.recurse` + an `include` glob
  that picks up `project.yaml` and every `*/application*.yaml` — but **not**
  `root.yaml` itself, so the app-of-apps never manages itself.
- It runs `automated` sync with `prune` + `selfHeal`, so committing a change
  under `mcp/` and pushing is all it takes to roll out.
- `root.yaml` lives in the always-present `default` project; the
  [`mcp` AppProject](project.yaml) (sync-wave `-1`, created first) governs the
  child Applications, which all carry `project: mcp`.

## Bootstrap

Once the repo is pushed to GitHub, apply the app-of-apps **once**:

```sh
kubectl apply -f mcp/root.yaml
```

From then on Argo CD keeps the `mcp` project and all child apps in sync from
git — no further manual `apply` is needed for this group. (If the GitHub repo
is private, register repo credentials in Argo CD first; a public HTTPS repo
needs none.)

Create each service's Secrets (see its `README.md`) before its first sync.

## Disabling a service (without deleting its manifest)

To stop deploying one app while keeping its manifest in git, add it to the
app-of-apps `exclude` glob in [`root.yaml`](root.yaml):

```yaml
    directory:
      recurse: true
      include: '{project.yaml,*/application*.yaml}'
      exclude: '{graphiti/application.yaml}'   # disabled apps, comma-separated
```

Commit + push, then **re-apply the app-of-apps** — `root.yaml` is excluded
from its own `include` (it never manages itself), so a git push alone does
**not** update the live app-of-apps spec:

```sh
kubectl apply -f mcp/root.yaml
```

Argo CD then drops that `Application`. Because every child carries the
`resources-finalizer.argocd.argoproj.io` finalizer, pruning the `Application`
**cascade-deletes its workloads** (Deployments, Services, Ingresses, …) for a
clean teardown. StatefulSet PVCs (e.g. graphiti's neo4j data) are retained, so
re-enabling — remove the path from `exclude`, push, and re-apply `root.yaml` —
redeploys and re-binds the existing data.

List several to disable more at once:
`exclude: '{graphiti/application.yaml,kubernetes/application.yaml}'`.
