# mcp/basic-memory

> **Storage update (2026-11-07):** The active notes PVC is `basic-memory-data-local` on `local-ssd` (worker-3 NVMe), shared directly with Ignis. The former `basic-memory-data-smb` PVC and its files are retained as a frozen pre-migration copy; they are no longer live or automatically updated. Historical SMB references below describe the previous layout. Back up the live notes separately from worker-3.

Basic Memory MCP server (`ghcr.io/basicmachines-co/basic-memory:1.0.0`),
single replica on `kube-worker-3`, reached through litellm's `basic_memory`
MCP server, which routes each user to their own instance (see
[Per-user routing behind litellm](#per-user-routing-behind-litellm)). There is no
ingress: since 2026-11-08 the only way in is
`https://litellm.whitediver.keenetic.link/basic_memory/mcp`.
The markdown note tree is SMB-backed; the SQLite index and the FastEmbed model
cache live on a separate `local-path` volume (see below).

The `nodeSelector` pins `kube-worker-3` (moved off the CPU-saturated
`kube-master` control-plane node; the PVC is SMB/network storage, so
relocating is data-safe).

Deployed as an `ApplicationSet` (`applicationset.yaml`): one Application per
list element, each with its own pod, Service and note volume, so
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
| Application, Helm release, Deployment, Service | `<name>` | `basic-memory` |
| note PVC (local-ssd) | `<name>-data-local` | `basic-memory-data-local` |
| index PVC (local-path) | `<name>-index` | `basic-memory-index` |
| server `--path` | `/mcp/<name>` | `/mcp/basic-memory` |
| router upstream | `http://<name>.mcp.svc.cluster.local:8000/mcp/<name>` | same |

**Never rename an existing element.** The new name maps to a new, empty PVC, and
the old Application is deleted (its resources are orphaned, see below), so the
notes would seem to be gone.

### Adding a family member

1. Add `- name: basic-memory-<member>` to `generators[0].list.elements`. Use lowercase
   letters, digits and `-` only: the name becomes a DNS label and a PVC name.
2. Give the member a routing token: add a line to the router's token Secret and
   restart the router (see [Per-user routing](#per-user-routing-behind-litellm)).
   The member stores the same token once in the litellm UI. Their
   `basic_memory-bm_*` tools then reach their own instance. No new litellm server
   is needed.
3. Optionally, add a `blackbox-mcp` target in
   [`observability/prometheus`](../../observability/prometheus/application.yaml).
4. For a browser UI, add the member's [Ignis](../ignis/README.md#adding-a-family-member)
   element with `vault: basic-memory-<member>-data-local`.

A **shared** instance (the planned `basic-memory-home`) is not routed by token,
because everyone should reach it. It gets its own litellm server instead,
`basic_memory_home` → `http://basic-memory-home.mcp.svc.cluster.local:8000/mcp/basic-memory-home`,
so its tools are `basic_memory_home-bm_*` next to the personal `basic_memory-bm_*`.

The new PVC lands in its own share folder
(`//192.168.99.44/smb-csi/pvc-mcp-basic-memory-<member>-data-smb`): `subDir` is
derived from the PVC name.

## Per-user routing behind litellm

```text
client (litellm OAuth/SSO or own key)
  -> litellm /basic_memory/mcp
       static header  X-BM-Token: ${BM_TOKEN}   (scope: user, value per litellm user_id)
  -> basic-memory-router :8000 /mcp           (application-router.yaml, nginx)
       map X-BM-Token -> instance name; unknown, empty or literal ${BM_TOKEN} -> 403
  -> <instance>.mcp.svc.cluster.local:8000/mcp/<instance>
```

Everyone uses the same litellm server and therefore the same tool names
(`basic_memory-bm_*`), so one `CLAUDE.md`, skill set and connector setup works for
the whole family. Only the token differs.

litellm has one `url` per MCP server, so it cannot choose a backend per user by
itself. What it can do (verified in the 1.108.1 source) is fill a `${NAME}`
placeholder in `static_headers` from a value stored per `user_id`. The router does
the rest.

**The token is the credential, not a label.** litellm lets every user set their
own `scope: user` value (`POST /v1/mcp/server/{server_id}/user-env-vars`, or the UI
page `/ui/mcp-servers?fill_env_vars=<server_id>`). If the value were the instance
name, anyone could type someone else's name. A random token works because nobody
can guess another member's token. The router drops `X-BM-Token` before it
proxies, so the token never reaches the pod.

The value is keyed by litellm `user_id`. A Claude connector or Claude Code that
signs in through litellm's OAuth bridge carries the member's SSO user id. A client
that can only send a static header (Copilot CLI) uses a virtual key created **for
that user** (`/key/generate` with `user_id`), so it resolves to the same stored
token. The master key resolves to `default_user_id`, which has no token, so it
gets `412`.

The values are tied to the server id. For a config-defined server litellm hashes
`name|url|transport|auth_type|alias`, so editing the `url` would mint a new id and
silently detach every stored token. `server_id` is therefore pinned in
`ai/litellm` to the id the tokens were stored under.

Upstream MCP sessions are keyed by the client's gateway session plus the auth
fingerprint (`_upstream_session_for` in `mcp_server_manager.py`), so two users never
share an upstream session. litellm skips its health and instructions probes for a
server that needs a per-user value, so `basic_memory` shows health `unknown`.

### The token map is an out-of-band Secret

The repo is public, so the map lives in Secret `basic-memory-router-tokens`
(namespace `mcp`), key `tokens.map`, one nginx `map` line per member. The pod does
not start without it.

```sh
TOK_SERGE=$(openssl rand -hex 32)
cat > tokens.map <<EOF
"$TOK_SERGE" basic-memory;
EOF
kubectl -n mcp create secret generic basic-memory-router-tokens --from-file=tokens.map \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n mcp rollout restart deploy/basic-memory-router
```

nginx reads the map only at start, so restart the router after every change. Tokens
are lowercase hex, so no quoting issues in the map.

`map_hash_bucket_size 128` is required. The default follows the CPU cache line,
64 bytes on these arm64 nodes, and a 64-character key does not fit: nginx exits
with `could not build map_hash, you should increase map_hash_bucket_size: 64`. The
first rollout crashlooped on exactly that, because the pre-merge test used a short
token.

### Storing a member's token in litellm

The server id of a config-defined server comes from `GET /v1/mcp/server`. Each
member stores their own value, signed in as themselves (UI page above, or
`POST /v1/mcp/server/<server_id>/user-env-vars` with
`{"values": {"BM_TOKEN": "<token>"}}` and their own key). Without a value, tool
calls return `412` with the setup URL.

### Cutover order (2026-11)

The `env_vars` declaration has to be live before anyone can store a value
(litellm accepts only declared names), so there is a short gap after the litellm
restart:

1. Create the Secret, merge. The router comes up next to the old path, idle.
2. litellm restarts with `basic_memory` → router. Until step 3 every
   `basic_memory` call gets `403`.
3. Store `TOK_SERGE` for Serge's SSO user (UI) **and** with the master key for
   `default_user_id`. The second one keeps the old
   `dev.whitediver.keenetic.link/mcp/basic-memory` path (Oathkeeper + master key)
   working while clients move off it.
4. Smoke test: `bm_search` through both paths; the router log shows `200`.

Rollback: revert the `basic_memory` block in `ai/litellm` to the direct
`http://basic-memory.mcp.svc.cluster.local:8000/mcp/basic-memory` URL.

### The old path is gone (2026-11-08)

Before 2026-11-08 every instance had an ingress `dev.whitediver.keenetic.link/mcp/<name>`
(shared `mcp-basic-auth` htpasswd) → `oathkeeper-proxy`, which injected the litellm
master key → litellm. Anyone with the shared credentials arrived as
`default_user_id` and so reached Serge's instance. Removed once nginx logs showed
only this workstation's Claude Code and Copilot CLI on it and both had moved:

- `ingress:` dropped from `applicationset.yaml`, then the live Ingress removed with
  a one-off pruning sync of just that resource. The generated Applications run
  `automated` with `selfHeal` only, **no `prune`**, so dropping something from the
  values leaves the live object in place (Application `OutOfSync`, resource
  `requiresPruning`). Automated prune was not turned on for this: in an app that
  holds notes, each removal is pruned deliberately instead:

  ```sh
  kubectl -n argo-cd patch application basic-memory --type merge -p \
    '{"operation":{"initiatedBy":{"username":"admin"},"sync":{"prune":true,"resources":[{"group":"networking.k8s.io","kind":"Ingress","name":"basic-memory","namespace":"mcp"}]}}}'
  ```

- rule `mcp-basic-memory` deleted from Secret `oathkeeper-rules`;
- the `default_user_id` value deleted
  (`DELETE /v1/mcp/server/<server_id>/user-env-vars` with the master key).

That ingress had buffering off and 3600 s read/send timeouts. The `litellm.*`
ingress that fronts this traffic now has 600 s timeouts and default buffering;
Claude Code and Copilot CLI worked over it, so nothing was carried over. Raise
those timeouts on the litellm ingress if long-lived streams start getting cut at
10 minutes.

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
`/mcp/basic-memory` prefix. We override `command`/`args` to run
`--transport streamable-http --path /mcp/<name>` so every URL the server
emits stays correctly prefixed (mirrors mcpo's `--path-prefix` approach). The
router proxies to exactly that path (`/mcp/<name>`), so no rewrite is needed.

## Semantic search is on, and cannot be turned off (since 0.24.0)

History: on 0.22.1 the FastEmbed/onnxruntime path died with SIGSEGV (exit 139) on
the arm64 workers (Raspberry Pi 5) a few minutes after start, right after it pulled
`bge-small-en-v1.5` from HuggingFace. That put the pod in CrashLoopBackOff (119
restarts) and took the whole MCP server down, so semantic search was switched off
with `BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED: "false"`.

0.24.0 removed that setting (upstream #1696): semantic search is always on and the
variable is ignored, so the upgrade past 0.23.x could only go ahead once FastEmbed was
proven not to crash here. On 2026-11-02 a throwaway canary pod ran 1.0.0
(onnxruntime 1.27.0, was 1.26.0) on `kube-worker-3` against a copy of the full vault
(745 notes): it embedded every note, 25,018 chunks, with no crash and no restart. The variable was
dropped from the manifest because it no longer does anything.

The cost is the first start on an empty index. Files are indexed in about 7 minutes,
and text search works from then on. The embeddings run at about 115 chunks a minute
with the CPU at the 2000m limit. The vault is ~25k chunks (a long note is ~90 chunks
and takes 2+ minutes), so the backlog takes about 3.5 hours: the canary started at
07:45 UTC and had every note embedded by 11:35. During that time an
MCP session plus a hybrid search still answered in about 1.2 s. The model
(~65 MB) and the vectors persist on the `index` volume, so later restarts do not
re-embed.

If a later image brings the crash back, the fallback is to compute embeddings off-box:
`BASIC_MEMORY_SEMANTIC_EMBEDDING_PROVIDER=openai` with
`BASIC_MEMORY_SEMANTIC_EMBEDDING_API_BASE` pointed at an OpenAI-compatible endpoint.
The `litellm` provider is no longer in the image: since 1.0 it is an optional extra.

## Image tag is pinned, not `latest`

`latest` is what broke this app: it silently moved onto a build whose
FastEmbed path segfaults on arm64, and `imagePullPolicy: Always` re-pulled it
on every restart, so there was no last-known-good to fall back to. The tag is
pinned to the exact release that was verified healthy here. That was first `0.22.1`
(`sha256:0da35f46…`), and since 2026-11-02 it is `1.0.0` (index `sha256:cad49f54…`),
which passed the canary described above. Upgrades are an explicit commit. Pin the digest as
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
| `requests.memory` | `1536Mi` | `1Gi` | 1.0.0 loads the FastEmbed model: over the canary's full embedding run the server process held 1.24–1.50Gi anon (RssAnon), ~1.28Gi once idle, against ~873Mi for 0.22.1 with semantic search off. The request is set at the observed peak so this pod isn't the first evicted under node pressure |
| `requests.cpu` | `500m` | `50m` | bursty — idle ~4m, but indexing peaks near 1880m |
| `limits.memory` | `2Gi` | `1536Mi` | the 1.50Gi anon peak alone would hit a 1536Mi cap. A `basic-memory status` or `doctor` exec'd into the pod runs in the same cgroup: one exec pushed the working set to ~1.7Gi, and with a `status` plus a sqlite query exec'd every 15 minutes for the whole run, `memory.events` still recorded no OOM kill under the 2Gi cap |
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
deeper (`/var/lib/basic-memory/state-v1`, see the 1.0 section for why not `state`):
basic-memory creates that directory itself,
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

## 1.0.0: `bm_*` tools and a fresh index directory (2026-11-02)

The jump from 0.22.1 to 1.0.0 crosses 0.23 and 0.24. What matters here:

- **The MCP tools are renamed and the old names are gone.** There are no aliases.
  `search_notes` → `bm_search`, `read_note` → `bm_read`, `write_note` → `bm_write`,
  `edit_note` → `bm_edit`, `build_context` → `bm_context`, `recent_activity` →
  `bm_project(command="recent")`, and so on. Behind litellm the tools are
  `basic_memory-bm_*`. Every note tool now requires `project` (`main`), even with a
  default project. Clients need updating together with the server: the Claude Code
  `basic-memory` plugin, prompts and allowlists that name the old tools. After the
  sync, clients reconnect to load the new schemas. `bm_project_delete` is opt-in and
  is not exposed.
- **Semantic search is always on.** See the section above.
- `BASIC_MEMORY_SYNC_CHANGES` was renamed `BASIC_MEMORY_INDEX_CHANGES` in 0.23.0.
  The old name was still auto-migrated, but the manifest now uses the new one.
- With a non-loopback `--host` the server logs a "no authentication" warning on
  every start. That is expected here: the pod has no ingress, and the only way in
  is litellm (OAuth or a virtual key) plus the router's token check. The DNS-rebinding guard (0.24.0) applies only to loopback
  binds.
- Streamable HTTP sessions now expire after 30 idle minutes (MCP SDK default). Clients
  open a new one.

**Why `state-v1` instead of `state`.** The 0.22.1 `memory.db` (~210 MB) would need
every 0.23/0.24/1.0 migration in place, on the Pi, inside the startup probe's
5-minute window. That path was not tested. A fresh directory is the path the canary
did test (empty index → full rebuild from the vault), and it keeps rollback a pure
`git revert`: 0.22.1 finds its `state/` untouched, with its own `config.json` and
database. `config.json` in `state-v1` is generated from `BASIC_MEMORY_HOME` and
`BASIC_MEMORY_DEFAULT_PROJECT`. The old `.bmignore` was the stock default, so nothing
custom is lost. The canary also confirmed that a full 1.0 index does not modify note
files: md5 of all 770 files was identical before and after.

Rollback window: once the new version is settled, `state/` can be deleted by hand
(`kubectl -n mcp exec deploy/basic-memory -- rm -rf /var/lib/basic-memory/state`).
After that, a rollback rebuilds the 0.22.1 index from scratch, which is still safe
because the index is derived data.

While the first start is indexing, do not write through MCP until the file stage is
done (`basic-memory status`: `files: idle`). A note that is not indexed yet can fail
to resolve by permalink, and the write then creates a duplicate.

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
