# mcp/basic-memory

Basic Memory MCP server (`ghcr.io/basicmachines-co/basic-memory:0.22.1`),
single replica on `kube-worker-3`, behind the `/mcp/basic-memory`
ingress on `dev.whitediver.keenetic.link` with `mcp-basic-auth` htpasswd.
The markdown note tree is SMB-backed; the SQLite index and embedding model
cache are rebuilt off-volume on each start.

The `nodeSelector` pins `kube-worker-3` (moved off the CPU-saturated
`kube-master` control-plane node; the PVC is SMB/network storage, so
relocating is data-safe).

Deployed as an `ApplicationSet` (`applicationset.yaml`): one Application per
list element, each with its own pod, Service, ingress path and note volume, so
every family member gets an independent note space. Today the list holds only
the original instance, `basic-memory`.

This file holds the rationale that, by repo convention, must **not** live as
comments inside `applicationset.yaml` (see the root `CLAUDE.md`).

## One instance per list element

Every per-instance name derives from the element's `name`. `name: basic-memory`
reproduces the names the instance had as a plain Application, which is what
keeps its data attached:

| piece | from `name` | original instance |
| --- | --- | --- |
| Application, Helm release, Deployment, Service, Ingress | `<name>` | `basic-memory` |
| note PVC (SMB) | `<name>-data-smb` | `basic-memory-data-smb` |
| index PVC (local-path) | `<name>-index` | `basic-memory-index` |
| public path / server `--path` | `/mcp/<name>` | `/mcp/basic-memory` |
| litellm alias (ingress `rewrite-target`) | `<name>` with `-` → `_` | `basic_memory` |

**Never rename an existing element.** The new name maps to a new, empty PVC, and
the old Application is deleted (its resources are orphaned, see below), so the
notes would seem to be gone.

### Adding a family member

1. Add `- name: basic-memory-<member>` to `generators[0].list.elements`. Use lowercase
   letters, digits and `-` only: the name becomes a DNS label and a PVC name.
2. Add the litellm MCP server to `mcp_servers` in
   [`ai/litellm`](../../ai/litellm/application.yaml):
   `basic_memory_<member>` → `http://basic-memory-<member>.mcp.svc.cluster.local:8000/mcp/basic-memory-<member>`.
   Without it the ingress routes to an alias litellm does not know.
3. Optionally, add a `blackbox-mcp` target in
   [`observability/prometheus`](../../observability/prometheus/application.yaml).
4. For a browser UI, add the member's [Ignis](../ignis/README.md#adding-a-family-member)
   element with `vault: basic-memory-<member>-data-smb`.

**Do not add an Oathkeeper rule.** The existing `mcp-basic-memory` rule matches
`/mcp/basic_memory<.*>`, so it already covers `/mcp/basic_memory_<member>`. A
second rule that matches the same URL makes Oathkeeper fail the request because
more than one rule matches. No router entry is needed either, because the host
does not change.

The new PVC lands in its own share folder
(`//192.168.99.44/smb-csi/pvc-mcp-basic-memory-<member>-data-smb`): `subDir` is
derived from the PVC name.

### Separate storage is not separate access

All instances sit behind the same `mcp-basic-auth` htpasswd, and Oathkeeper
injects the same litellm master key for all of them. Anyone with the shared
basic-auth credentials can therefore reach every member's instance. To make an
instance private, give its ingress its own htpasswd Secret (`auth-secret`) and
scope litellm access (drop `allow_all_keys`, use per-member virtual keys).

## Deleting never cascades (`preserveResourcesOnDeletion`)

`syncPolicy.preserveResourcesOnDeletion: true` makes the controller generate the
Applications **without** `resources-finalizer.argocd.argoproj.io`. Deleting an
element, the ApplicationSet, or a generated Application therefore leaves the
Deployment, the PVCs and the notes in place. Teardown of a member is manual and
deliberate: delete the orphaned workload by hand, and the SMB PV stays `Retain`
anyway. The note PVC also carries `helm.sh/resource-policy: keep` (`retain: true`),
which Argo honours on deletion.

## The StorageClass belongs to the app-of-apps

`basic-memory-smb` is shared by every instance, so no generated Application may
own it: two Applications rendering the same cluster-scoped object fight over its
tracking annotation. It is the second YAML document in `applicationset.yaml`,
applied directly by the `mcp` app-of-apps. The file name still matches the
`*/application*.yaml` include glob, so `bootstrap.yaml` needed no change. The
class body is byte-for-byte what `rawResources.smb` used to render. That matters
because `parameters` are immutable. Deleting the class would not touch existing
volumes: the PV carries its own source, `subDir`, mount options and secret refs.

## Migration from a plain Application (2026-11-01)

The migration took two commits, because the `mcp` app-of-apps runs with
`prune: true` and the old Application had the resources finalizer. Deleting
`application.yaml` in the same commit that adds the ApplicationSet would have
let the parent prune the Application, cascading into the Deployment and PVCs
before the ApplicationSet controller could adopt it.

1. `application.yaml` got `argocd.argoproj.io/sync-options: Prune=false`, and
   the finalizer was dropped. This step had to be live before step 2.
2. `application.yaml` was deleted and `applicationset.yaml` added. The parent
   skipped the prune. The ApplicationSet controller adopted the same-named
   Application: per `createOrUpdateInCluster` in Argo CD 3.5 it replaces
   annotations (dropping the `mcp` tracking-id and `Prune=false`), labels and
   finalizers, and sets itself as the controller owner. From then on the parent
   no longer tracks the Application.

The rendered manifests were diffed before the switch (`helm template` of old vs
new values): identical apart from the StorageClass, so adoption changed no live
object. A tarball of `/app/data` (694 files, 671 `.md`) was taken first.

## Why `streamable-http` mounted at the full external path

The image default CMD is `mcp --transport sse`, whose SSE message callback is
advertised at the server root (`/messages/`) and so bypasses the
`/mcp/basic-memory` ingress prefix. We override `command`/`args` to run
`--transport streamable-http --path /mcp/basic-memory` so every URL the server
emits stays correctly prefixed (mirrors mcpo's `--path-prefix` approach). The
ingress therefore needs **no rewrite** — the app already serves at this exact
path, so the original URL is passed straight through.

## Ingress routes through litellm (usage stats)

The ingress `rewrite-target` sends traffic to litellm's MCP gateway via
`oathkeeper-proxy` (which injects the litellm key) instead of straight to the
pod, so tool calls land in litellm's per-tool usage stats. The rewrite maps the
external `/mcp/basic-memory` onto the litellm alias `/mcp/basic_memory`
(hyphen→underscore — litellm rejects `-` in a server name). Buffering is off and
read/send timeouts are raised to 3600s because MCP streamable-http holds
long-lived responses that nginx would otherwise cut. See
[`../../platform/oathkeeper`](../../platform/oathkeeper).

## Semantic search is off (FastEmbed segfaults on arm64)

`BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED: "false"`. The cluster workers are
arm64 (Raspberry Pi 5); the FastEmbed/onnxruntime path in the image
dies with SIGSEGV (exit 139) a few minutes after start, right after it pulls
`bge-small-en-v1.5` from HuggingFace — which put the pod in CrashLoopBackOff
(119 restarts) and took the whole MCP server down, not just semantic search.
Search falls back to the SQLite full-text index, which is what `search_notes`
uses anyway.

The `BASIC_MEMORY_SEMANTIC_EMBEDDING_{PROVIDER,MODEL}` and
`_MIN_SIMILARITY` vars were dropped with it — they only apply when semantic
search is enabled. To bring it back, embeddings have to be computed off-box
(e.g. an OpenAI-compatible provider pointed at in-cluster litellm), not by
onnxruntime on the Pi.

## Image tag is pinned, not `latest`

`latest` is what broke this app: it silently moved onto a build whose
FastEmbed path segfaults on arm64, and `imagePullPolicy: Always` re-pulled it
on every restart, so there was no last-known-good to fall back to. The tag is
pinned to the exact release that was verified healthy here — `0.22.1`, which
at the time of pinning was byte-identical to `latest`
(`sha256:0da35f46…`). Upgrades are now an explicit commit. Pin the digest as
well (`tag: "0.22.1@sha256:…"`) only if an upstream release tag is ever found
to have been re-pushed.

`pullPolicy: IfNotPresent` has to be stated explicitly, not left to the
kubelet default. The chart only emits the field when it is set, so while the
tag was `latest` the API server defaulted it to `Always` — and that defaulted
value is owned by no applier, so `ServerSideApply` never prunes it. Changing
the tag alone left the pod still re-pulling from GHCR on every restart, which
makes a restart depend on the registry being reachable.

## Resource sizing — measured, not guessed

| setting | value | was | why |
| --- | --- | --- | --- |
| `requests.memory` | `1Gi` | `256Mi` | actual draw is ~943Mi; the request is matched to it so this pod isn't the first evicted under node pressure |
| `requests.cpu` | `500m` | `50m` | bursty — idle ~4m, but indexing peaks near 1880m |
| `limits.memory` | `1536Mi` | `1Gi` | 943Mi actual was riding the OOM edge at a 1Gi cap |
| `limits.cpu` | `2000m` | *(none)* | new cap — previously unlimited, and indexing pegged `kube-worker-3` at 1880m / 82% |

The CPU limit is the one to be careful with: it was added to stop indexing from
starving the rest of `kube-worker-3`, not because the workload wants less CPU.
Lowering it further trades node headroom for slower indexing.

## SMB volume + StorageClass naming

No out-of-band files: the data volume is managed by the generated Application
and its `StorageClass` by the app-of-apps (see above). Until the ApplicationSet
both were `app-template` items, so both names were pinned with `forceRename`:
the class is `basic-memory-smb` and the PVC is `basic-memory-data-smb`.
app-template 4+ has no `nameOverride` and appends the item key to the release
name only when there is more than one item of that kind, so unpinned the class
(the only raw resource) would render as `basic-memory` and the PVC as
`basic-memory-data`. Neither rename is harmless: a PVC's `storageClassName` is
immutable, and [`../ignis`](../ignis/README.md) mounts `basic-memory-data-smb`
by name.

The CSI `subDir` is derived from the PVC namespace+name
(`pvc-mcp-basic-memory-data-smb`), so the share folder is independent of the
class name. Only the markdown note tree lives here; the SQLite index lives on
the separate `index` volume below. `uid/gid=1000` matches the image's appuser.

## The SQLite index lives on its own volume, not in the container (2026-09-16)

`BASIC_MEMORY_HOME` (`/app/data`, the notes themselves) is the SMB volume above, but the
search index is separate: basic-memory keeps `memory.db` in `$HOME/.basic-memory`
(`/home/appuser/.basic-memory`), which was the **container's writable layer** — i.e. the
node's SD card. Measured 2026-09-16: 86 MB of `memory.db` and ~750 MB written to the SD
in 8 hours, because the whole index is rebuilt on every restart.

The `index` persistence entry moves that directory onto a `local-path` PVC. On
kube-worker-3 `local-path` now resolves to the USB SSD (see
[`platform/local-path`](../../platform/local-path/README.md)), and the class is
`Delete`, so the volume goes away with the claim.

**Mount the volume one level above the config dir.** basic-memory calls
`chmod 0700` on its config dir at startup, and the directory a `local-path` volume
gives you is created by the provisioner's helper pod as `root` with mode `0777`.
`chmod` by the container's uid 1000 on a root-owned directory is `EPERM`, so mounting
the PVC straight at `~/.basic-memory` crashlooped the pod. Instead the volume is
mounted at `/var/lib/basic-memory` and `BASIC_MEMORY_CONFIG_DIR` points one level
deeper (`/var/lib/basic-memory/state`): basic-memory creates that directory itself,
owns it, and may chmod it. The same trap applies to any image that chmods its own
data directory.

The index is derived data — it is rebuilt from the notes on the SMB volume. The first
start after this change re-syncs for a few minutes; `config.json` is recreated from
`BASIC_MEMORY_HOME` / `BASIC_MEMORY_DEFAULT_PROJECT`. During that window edits made
through the MCP tools can fail to resolve a note by permalink and silently create a
duplicate, so wait for the sync to finish before writing.

## app-template 5: the Deployment is recreated once (2026-09-24)

app-template 4+ selects pods by `app.kubernetes.io/controller` instead of
`app.kubernetes.io/component`, and a Deployment's `spec.selector` is immutable,
so the 3.6.0 → 5.x bump cannot be applied in place. It goes in two commits:

1. `replicas: 0` plus the controller annotation
   `argocd.argoproj.io/sync-options: Force=true,Replace=true`. Argo deletes the
   Deployment and creates it with the new selector. The delete does not wait
   for the old pod, so at one replica the old and the new pod could briefly
   share the volumes — which the chart's default `Recreate` strategy otherwise
   prevents. At zero replicas there is no new pod to overlap.
2. Once the old pod is gone: `replicas` back to the default and the annotation
   dropped, in the same commit. Argo reads the option from the live object as
   well, so this sync is one more forced replace — harmless at zero replicas —
   and it leaves a Deployment without the annotation. Dropping the annotation
   without a spec change is not enough: under server-side apply Argo's diff
   ignores fields its own field manager does not own, so the removal may not
   register, and the live annotation would turn every later sync into a
   delete-and-recreate.

The service is down between the two merges.

## Connected but no tools: check the master's disk first (2026-10-21)

Symptom: Claude Code lists basic-memory with no tools, litellm logs `Timeout while
listing tools from basic_memory`, a direct `initialize` takes ~60 s while `tools/list` on
the same session answers in 0.1 s, and `ls /app/data` is slow or fails with `Resource
temporarily unavailable`. The cause was an unreadable sector on `kube-master`'s USB HDD —
the disk behind every SMB-CSI volume — that pigallery2 kept re-reading; see
[`media/pigallery2`](../../media/pigallery2/README.md#held-at-replicas-0-until-the-masters-hdd-is-replaced-2026-10-21).
Start with `dmesg | grep "critical medium error"` on kube-master.

The stuck `smbd` had this vault's `claude/` directory open, which pointed at the wrong
app: all SMB mounts of one node share a single SMB session, so one `smbd[<node-ip>]`
process serves every pod on that node, and its open handles do not say which pod is
blocked. Find the blocked reader on the node side (a process in `D` state inside the pods).
