# observability

Metrics / logs apps for the cluster: `cloud-billing`, `grafana`, `influxdb`,
`keenetic-grafana-monitoring`, `prometheus`. Group AppProject is
`observability`; the app-of-apps (`bootstrap.yaml`) stays in `default` so it can
create that project (see the root `README.md` for the general app-of-apps
model).

`cloud-billing/` is the one directory here holding more than one Application: it
ships the AWS and GCP exporters that feed cloud resource + spend metrics into
`prometheus`. See [`cloud-billing/README.md`](cloud-billing/README.md).

## What's GitOps-managed vs push-based

`keenetic-grafana-monitoring`, `grafana` and `cloud-billing/yace` are reconciled
by the app-of-apps. `grafana` runs in `project: default` (its destination
namespace isn't in the `observability` AppProject whitelist), but it still
matches the `bootstrap.yaml` `include` glob so it's reconciled. `influxdb`,
`prometheus` and `cloud-billing/stackdriver-exporter` are held back by the
`bootstrap.yaml` `exclude` glob and stay **push-based** — apply each by hand:

```sh
kubectl apply -f observability/influxdb/application.yaml
kubectl apply -f observability/prometheus/application.yaml
kubectl apply -f observability/cloud-billing/application-stackdriver-exporter.yaml
```

`influxdb` and `prometheus` stay in `project: default`;
`cloud-billing/stackdriver-exporter` is in `project: observability` (its
namespace is whitelisted) and is held back only until the GCP project exists —
see its README.

## Sync ordering

`project.yaml` carries `sync-wave: "-1"` so the `observability` AppProject is
created before the child Applications that reference it — the app-of-apps
applies both in one sync, and the children would fail without the project.

## sourceRepos

The project whitelists two repos: this one (the app-of-apps reads child
Application manifests from it) and `Arbuzov/home-k8s-helm`, the local
wrapper chart that the `keenetic-grafana-monitoring` Application pulls from.
