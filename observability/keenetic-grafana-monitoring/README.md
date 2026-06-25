# keenetic-grafana-monitoring

Polls a Keenetic router and writes metrics into InfluxDB (visualized in
Grafana). Deployed via the `observability` app-of-apps from the wrapper chart in
`Arbuzov/home-cluster-helm` → `arbuzov/observability/keenetic-grafana-monitoring`.

## Source

`repoURL` points at the local wrapper chart in `home-cluster-helm`, not an
upstream chart. The earlier `helm.influxdata.com/influxdb@0.1.0` reference was
bogus and never resolved — don't switch back to it.

## Secret

`config.ini` carries the router password and the InfluxDB token, so it is
mounted from a Secret created out-of-band (never in git). Create it before the
first sync — the pod stays `ContainerCreating` until it exists:

```sh
cat > config.ini <<'INI'
[influx2]
url=http://192.168.99.44:8086
org=-
# influx v1.x: "username:password"
token=INFLUX_USER:INFLUX_PASSWORD
timeout=6000
bucket=keenetic
[keenetic]
admin_endpoint=http://192.168.99.1:80
skip_auth=false
login=admin
password=ROUTER_PASSWORD
[collector]
interval_sec=30
INI

kubectl create secret generic keenetic-grafana-monitoring-config \
  --namespace influxdb \
  --from-file=config.ini=./config.ini
```

## InfluxDB database

The exporter writes to the InfluxDB v1.x database named by `bucket` (`keenetic`).
It must exist first, or the pod CrashLoopBackOffs on the first write
(`404 database not found`):

```sh
kubectl exec -n influxdb influxdb-0 -- influx -execute 'CREATE DATABASE keenetic'
```
