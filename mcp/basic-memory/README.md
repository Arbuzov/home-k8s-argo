# mcp/basic-memory

Basic Memory MCP server (`ghcr.io/basicmachines-co/basic-memory:0.22.1`),
single replica on `kube-worker-3`, behind the `/mcp/basic-memory`
ingress on `dev.whitediver.keenetic.link` with `mcp-basic-auth` htpasswd.
The markdown note tree is SMB-backed; the SQLite index and embedding model
cache are rebuilt off-volume on each start.

The `nodeSelector` pins `kube-worker-3` (moved off the CPU-saturated
`kube-master` control-plane node; the PVC is SMB/network storage, so
relocating is data-safe).

This file holds the rationale that, by repo convention, must **not** live as
comments inside `application.yaml` (see the root `CLAUDE.md`).

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

The data volume and its `StorageClass` are both managed by this one
Application (no out-of-band files). Both names are pinned with `forceRename`:
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
