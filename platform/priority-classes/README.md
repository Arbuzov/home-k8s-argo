# priority-classes

Cluster-scoped `PriorityClass` objects. Split out from any single service so the
classes exist before anything references them.

## Why this exists

A pod that names a `priorityClassName` which does not exist in the cluster is
**rejected at admission**. So the class has to be created first, and it has to
live somewhere that is not the service it protects — otherwise the very first
sync of that service is the one that fails.

There is deliberately **no** `sync-wave` annotation here, and that is worth
stating because a negative wave is the obvious-looking thing to reach for.

A wave orders resources within a single sync of one app-of-apps. The first
consumer (`networking/wg-vless-gateway`) lives in a *different* one
(`networking/bootstrap.yaml`), so a wave on this Application could never order
anything relative to it. Worse, a negative wave would place this Application
ahead of the `platform` AppProject it declares (`project.yaml`, wave `-1`): on a
from-scratch bootstrap the parent sync would stall on an Application whose
project does not yet exist.

The ordering guarantee is the two-step procedure in "Adding a consumer" below,
and nothing else.

## `homelab-platform` (800000)

For workloads whose loss is a household outage rather than a degraded
convenience. The first consumer is the cascade VPN gateway
(`vpn/wg-vless-gateway`), which carries the entire home LAN's egress:
Keenetic → WireGuard → xray → VLESS/Reality → relay.

Two properties matter, and only one of them is about scheduling:

- **Eviction ranking.** Under node memory pressure the kubelet ranks pods by
  whether they exceed their requests, then by priority, then by usage over
  requests. A higher priority means this pod is evicted later than the media,
  MCP and AI workloads it shares a node with. This is the property actually
  being bought.
- **Scheduling queue order** when nodes are full on requests.

What it deliberately does **not** do is preempt.

### `preemptionPolicy: Never`

On a four-node cluster where only one node has meaningful spare capacity,
preemption buys very little and costs exactly the churn that saturates the
control plane in the first place — a preempted pod is a new image pull, a new
sandbox and a new burst of probe traffic. The class protects its holder from
being evicted; it does not evict anybody else.

### Value 800000

Chosen to sit far below the two built-ins so nothing here can ever displace the
control plane or the CNI:

| Class | Value |
| --- | --- |
| `system-node-critical` | 2000001000 |
| `system-cluster-critical` | 2000000000 |
| `homelab-platform` | 800000 |
| unlabelled pods (cluster default) | 0 |

`globalDefault: false` — no class here changes the priority of any pod that does
not name it. Today's unlabelled pods keep priority 0 and behave exactly as
before, so adding this Application is a no-op until a consumer opts in.

## Adding a consumer

Two steps, in this order. Doing them in one push risks the consumer syncing
before the class is established, which takes the workload down instead of
protecting it.

1. Sync this Application. Verify: `kubectl get priorityclass homelab-platform`.
2. Only then set `priorityClassName: homelab-platform` on the workload.

For `wg-vless-gateway` step 2 is a one-line change to `values.yaml` in the
`home-k8s-helm` repo; that chart already carries the template support and
defaults the value to empty.
