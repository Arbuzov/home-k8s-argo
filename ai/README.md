# ai

AI / automation app-of-apps. `bootstrap.yaml` is the group app-of-apps;
`project.yaml` is the `ai` AppProject; child services (currently `n8n`)
ship their own `application.yaml`. Delivery model, the `default`-vs-`ai`
project split, the self-excluding `include` glob, and the per-service
Secrets table are all in the root [`README.md`](../README.md) — not
repeated here.

## `project.yaml`

### `sync-wave: "-1"`

The app-of-apps applies the AppProject and the child Applications in one
sync. The wave forces `project.yaml` ahead so the `ai` project exists
before any child that declares `project: ai` is reconciled — otherwise
the children fail validation on the first sync.

### `sourceRepos`

Two entries, both required:

- `github.com/Arbuzov/local-cluster-argo.git` — where the app-of-apps
  reads the child `Application` manifests from.
- `8gears.container-registry.com/library` — the OCI Helm registry the
  `n8n` Application pulls its chart from.

Drop either and the corresponding sync is rejected by the AppProject.
