# mcp

Model Context Protocol servers, deployed to the `mcp` namespace. Unlike the
other groups (which are applied push-based with `kubectl apply`), this group
is **pull-based GitOps**: Argo CD watches this repo on GitHub and reconciles
everything under `mcp/` automatically.

## Pieces

| File | Kind | Purpose |
| --- | --- | --- |
| [`project.yaml`](project.yaml) | `AppProject` | The `mcp` project — restricts sources/destinations for the group |
| [`bootstrap.yaml`](bootstrap.yaml) | `Application` | The **app-of-apps** — deploys `project.yaml` + every child app |
| `<service>/application*.yaml` | `Application` | One MCP server each (`project: mcp`) |

Services: `atlassian` (Jira + Confluence), `basic-memory`, `gitlab`,
`graphiti`, `homeassistant`, `kubernetes`, `mcpo`. Each has its own
`README.md` for the out-of-band Secrets it expects.

The `mcp` AppProject's `sourceRepos` whitelists the two Helm chart repos the
child Applications pull from — `https://arbuzov.github.io/mcp-helm/` and
`https://bjw-s-labs.github.io/helm-charts` — alongside this repo (the
app-of-apps source). Adding a child whose chart lives elsewhere means adding
its repo here first, or the project rejects the sync.

## How it deploys (app-of-apps)

- [`bootstrap.yaml`](bootstrap.yaml) is the app-of-apps. Its source is this repo on
  GitHub over **HTTPS** (`https://github.com/Arbuzov/home-k8s-argo.git`,
  branch `main`), path `mcp`, with `directory.recurse` + an `include` glob
  that picks up `project.yaml` and every `*/application*.yaml` — but **not**
  `bootstrap.yaml` itself, so the app-of-apps never manages itself.
- It runs `automated` sync with `prune` + `selfHeal`, so committing a change
  under `mcp/` and pushing is all it takes to roll out.
- `bootstrap.yaml` lives in the always-present `default` project; the
  [`mcp` AppProject](project.yaml) (sync-wave `-1`, created first) governs the
  child Applications, which all carry `project: mcp`.

## Bootstrap

Once the repo is pushed to GitHub, apply the app-of-apps **once**:

```sh
kubectl apply -f mcp/bootstrap.yaml
```

From then on Argo CD keeps the `mcp` project and all child apps in sync from
git — no further manual `apply` is needed for this group. (If the GitHub repo
is private, register repo credentials in Argo CD first; a public HTTPS repo
needs none.)

Create each service's Secrets (see its `README.md`) before its first sync.

## Disabling a service (without deleting its manifest)

To stop deploying one app while keeping its manifest in git, add it to the
app-of-apps `exclude` glob in [`bootstrap.yaml`](bootstrap.yaml):

```yaml
    directory:
      recurse: true
      include: '{project.yaml,*/application*.yaml}'
      exclude: '{graphiti/application.yaml}'   # disabled apps, comma-separated
```

Commit + push, then **re-apply the app-of-apps** — `bootstrap.yaml` is excluded
from its own `include` (it never manages itself), so a git push alone does
**not** update the live app-of-apps spec:

```sh
kubectl apply -f mcp/bootstrap.yaml
```

Argo CD then drops that `Application`. Because every child carries the
`resources-finalizer.argocd.argoproj.io` finalizer, pruning the `Application`
**cascade-deletes its workloads** (Deployments, Services, Ingresses, …) for a
clean teardown. StatefulSet PVCs (e.g. graphiti's neo4j data) are retained, so
re-enabling — remove the path from `exclude`, push, and re-apply `bootstrap.yaml` —
redeploys and re-binds the existing data.

List several to disable more at once:
`exclude: '{graphiti/application.yaml,kubernetes/application.yaml}'`.

### Currently excluded

`graphiti`, `homeassistant`, and `kubernetes` are held back in the `exclude`
glob. `homeassistant` and `kubernetes` use `mcp-helm` charts that currently run
the server in **stdio** mode, which crashloops under Argo CD — they need a
stdio→SSE (HTTP) bridge before they can be enabled. `graphiti` is a different
chart (`bjw-s` `app-template`, a neo4j-backed server) and is held back
separately. Remove each from `exclude` once it's ready to deploy.
