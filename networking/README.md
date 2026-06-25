# networking

`AppProject` for the networking / VPN gateway apps (`openconnect-gateway`,
`wg-vless-gateway`, `wstunnel`). Applied by hand — this group is **not**
wired into an app-of-apps, so there is no `bootstrap.yaml`.

## `sourceRepos` — two repos whitelisted

The `Application` manifests for this group live in this repo
(`local-cluster-argo`); their Helm charts are pulled from a separate chart
repo. All three children source from `home-cluster-helm` (paths
`arbuzov/networking/{openconnect-gateway,wg-vless-gateway,wstunnel}`). The
project therefore whitelists both:

- `Arbuzov/local-cluster-argo` — holds these `Application` manifests
- `Arbuzov/home-cluster-helm` — holds the charts for all three apps
