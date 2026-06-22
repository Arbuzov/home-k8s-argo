# media

Media services, each in its own namespace. Like `apps/` and `mcp/`, this group
is **pull-based GitOps**: Argo CD watches this repo and reconciles everything
under `media/` automatically.

## Pieces

| File | Kind | Purpose |
| --- | --- | --- |
| [`project.yaml`](project.yaml) | `AppProject` | The `media` project — restricts sources/destinations for the group |
| [`bootstrap.yaml`](bootstrap.yaml) | `Application` | The **app-of-apps** — deploys `project.yaml` + every enabled child app |
| `<service>/application.yaml` | `Application` | One app each (`project: media`) |

Services: `photoprism` (serves `photos.whitediver.keenetic.link`) and
`opds-shelf` (`dev.whitediver.keenetic.link/{calibre-web,calibre,opds}`) are
deployed; `jellyfin` and `pigallery2` are **kept in git but held back** via the
`exclude` glob in `bootstrap.yaml`. The app-of-apps itself stays in the
`default` project so it can create the `media` AppProject.

> `project.yaml` deliberately carries **no `sync-wave`**. With `sync-wave: -1`
> Argo applies the AppProject first and then waits for it to report *healthy*
> before the wave-0 resources — but AppProjects expose no health, so the media
> sync deadlocks ("waiting for healthy state of AppProject/media"). Without the
> wave, the project applies in the same batch as everything else (it already
> exists, so ordering is moot).

## Bootstrap

```sh
kubectl apply -f media/bootstrap.yaml
```

## Enabling / disabling a service

The `exclude` glob in [`bootstrap.yaml`](bootstrap.yaml) is the on/off switch
(not `replicas: 0`). Remove a service from `exclude` to deploy it; add it to
disable it. After editing, **re-apply the app-of-apps** — `bootstrap.yaml` is
excluded from its own `include`, so a git push alone does **not** update the
live app-of-apps spec:

```sh
kubectl apply -f media/bootstrap.yaml
```

## Data safety on disable

When a service is excluded, the app-of-apps **prunes** its child Application.
The children carry the Argo cascade finalizer
(`resources-finalizer.argocd.argoproj.io`), so the prune deletes the workload
(Deployment, Service, PVC, …). That is safe for data because **every media data
volume is `Retain`**:

| PV / share | Class | Reclaim |
| --- | --- | --- |
| `jellyfin-config` | `smb-jellyfin` | Retain |
| `pigallery2-config` | `smb` | Retain |
| `pigallery2-photos` (`//192.168.99.44/pictures`) | static | Retain |
| `photoprism` config + `photoprism-pictures` PV | `smb` / static | Retain |

So a prune deletes the *claim* but the PV goes `Released` and the underlying SMB
data is kept. Re-enabling a service binds a fresh claim — to reattach the old
data, re-point the new PVC at the retained PV (clear its `claimRef`).
