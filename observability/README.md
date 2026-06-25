# observability

Metrics / logs apps for the cluster: `influxdb`, `keenetic-grafana-monitoring`,
`prometheus`. Group AppProject is `observability`; the app-of-apps
(`bootstrap.yaml`) stays in `default` so it can create that project (see the
root `README.md` for the general app-of-apps model).

## What's GitOps-managed vs push-based

Only `keenetic-grafana-monitoring` is reconciled by the app-of-apps. `influxdb`
and `prometheus` are held back by the `bootstrap.yaml` `exclude` glob and stay
**push-based** in `project: default` — apply each by hand:

```sh
kubectl apply -f observability/influxdb/application.yaml
kubectl apply -f observability/prometheus/application.yaml
```

## Sync ordering

`project.yaml` carries `sync-wave: "-1"` so the `observability` AppProject is
created before the child Applications that reference it — the app-of-apps
applies both in one sync, and the children would fail without the project.

## sourceRepos

The project whitelists two repos: this one (the app-of-apps reads child
Application manifests from it) and `Arbuzov/home-cluster-helm`, the local
wrapper chart that the `keenetic-grafana-monitoring` Application pulls from.
