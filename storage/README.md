# storage

Storage backends for the cluster. Pull-based GitOps via `bootstrap.yaml`
(app-of-apps), like `apps/`, `media/`, `mcp/` and `platform/`.

## Layout

- `smb/` — in-cluster Samba server at `192.168.99.44`, source of the
  `smb`/`smb-csi` shares most workloads mount (see `smb/README.md`).

## Delivery

`bootstrap.yaml` points Argo CD at this repo on GitHub and reconciles every
`<service>/application.yaml` automatically. There is **no AppProject** for this
group, so the app-of-apps and its children all stay in `project: default`.

Bootstrap once, after the repo is pushed to GitHub:

```sh
kubectl apply -f storage/bootstrap.yaml
```

From then on, edit a `storage/<service>/application.yaml`, commit and push to
`main`, and Argo CD syncs it on its own (`automated` sync with `prune` +
`selfHeal`). Toggle a service with `/enable` / `/disable` — they rename the
manifest in/out of the `*/application*.yaml` include glob; the app-of-apps
itself never needs re-applying. (`bootstrap.yaml` sits at the `storage/` root,
so the `*/application*.yaml` glob never matches it and it doesn't manage
itself; editing the glob itself would need a manual re-apply, renaming does
not.)

Out-of-band Secrets each service expects are listed in its own `README.md`
(`smb` needs `smbcreds` in namespace `smb` before its first sync).
