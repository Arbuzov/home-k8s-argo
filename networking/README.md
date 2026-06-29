# networking

`AppProject` + `bootstrap.yaml` app-of-apps for the networking / VPN gateway
apps (`openconnect-gateway`, `wg-vless-gateway`, `wstunnel`). The
`Application` manifests live in this repo (`home-k8s-argo`); their Helm
charts are pulled from the separate `home-k8s-helm` chart repo.

## Delivery — app-of-apps, with two children held back

`bootstrap.yaml` points Argo CD at this group on GitHub and reconciles
`project.yaml` plus every enabled child `Application` on its own (`automated`
sync, `prune` + `selfHeal`). Bootstrap once:

```sh
kubectl apply -f networking/bootstrap.yaml
```

| Child                  | Namespace  | Delivery                                     |
| ---------------------- | ---------- | -------------------------------------------- |
| `wg-vless-gateway`     | `vpn`      | app-of-apps (enabled)                        |
| `openconnect-gateway`  | `mcp`      | push-based — held back by the `exclude` glob |
| `wstunnel`             | `wstunnel` | push-based — held back by the `exclude` glob |

Only `wg-vless-gateway` is wired into the app-of-apps. The other two stay
push-based — apply them directly when they change:

```sh
kubectl apply -f networking/openconnect-gateway/application.yaml
kubectl apply -f networking/wstunnel/application.yaml
```

To wire either one in, drop it from the `exclude` glob in `bootstrap.yaml`.

## `sourceRepos` — two repos whitelisted

The `Application` manifests for this group live in `home-k8s-argo`; their
charts come from `home-k8s-helm` (paths
`arbuzov/networking/{openconnect-gateway,wg-vless-gateway,wstunnel}`). The
project therefore whitelists both:

- `Arbuzov/home-k8s-argo` — holds these `Application` manifests
- `Arbuzov/home-k8s-helm` — holds the charts for all three apps
