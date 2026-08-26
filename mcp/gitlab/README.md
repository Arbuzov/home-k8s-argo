# mcp-gitlab

GitLab MCP server ([zereight050/gitlab-mcp](https://hub.docker.com/r/zereight050/gitlab-mcp),
v2.1.20) at `/mcp/gitlab` on `dev.whitediver.keenetic.link`, talking to a
self-hosted GitLab behind a corporate VPN over the shared
[`openconnect-gateway`](../../networking/openconnect-gateway/).

> **Employer-specific routing is not in git.** The real GitLab URL, the
> VPN subnet, and the `/etc/hosts` pin live only in out-of-band Secrets and a
> local overlay (this app is held back from the app-of-apps and applied
> push-based — see **Out-of-band routing** below). The committed manifest
> carries placeholders only.

## Image & auth (2.1.x: remote-auth + stateless + token-injector)

The maintained image is `zereight050/gitlab-mcp` (the older `iwakitakuma/gitlab-mcp`
froze at 2.0.19). On 2.1.x a static server-side `GITLAB_PERSONAL_ACCESS_TOKEN` over
`STREAMABLE_HTTP` is **rejected at startup** — the server requires
`REMOTE_AUTHORIZATION=true` (token per request via a `Private-Token` header) or full
OAuth. We use `REMOTE_AUTHORIZATION` and supply the token from inside the pod, so
clients stay unchanged:

- **`token-injector` sidecar** (`nginx`) owns the chart's service port (3002 — the
  named `http` port the Service and probes target) and reverse-proxies to the app on
  `127.0.0.1:3003` (`PORT=3003`), adding `Private-Token` read from the
  `mcp-gitlab-credentials` Secret. Injection happens below the Service, so every
  client and route works with no change and the PAT never lands in git.
  `proxy_buffering off` + HTTP/1.1 keep the streamable-HTTP/SSE responses flowing.
- **`OAUTH_STATELESS_MODE=true`** seals the session into the `Mcp-Session-Id`
  (`v1.sid.…`) with the stable `OAUTH_STATELESS_SECRET`, so sessions **survive pod
  restarts** — clients don't have to reconnect/re-initialise. (Without it a restart
  drops the in-memory session and clients hang on the stale id until a manual
  `/mcp` reconnect.)
- **`HOST=0.0.0.0`** is required: 2.1.x defaults `HOST` to `127.0.0.1` (since 2.0.21),
  which would make the app unreachable from the injector and the probes.

The app container holds no GitLab token; `mcp-gitlab-credentials` is read only by the
injector. To roll back to the 2.0.x static-PAT model, `git revert` the bump — Argo
returns to `iwakitakuma/gitlab-mcp:2.0.19` (both Secrets are left intact).

## Corp routing (shared VPN gateway)

GitLab lives behind a corporate VPN. Like the confluence app, this pod does **not**
run its own OpenConnect tunnel — it routes the corp subnet through the shared
[`openconnect-gateway`](../../networking/openconnect-gateway/) pod (reusing the
`mcp-atlassian-vpn-credentials` Secret):

- a **`route-manager`** sidecar keeps `ip route replace <corp-subnet> via
  <gateway-pod-ip>` pointed at the gateway's headless Service. The subnet is
  injected as `CORP_CIDR` from the shared `mcp-corp-config` Secret, so it never
  lands in git;
- **`podAffinity`** co-locates this pod with the gateway, because the route's
  pod-IP next-hop is only on-link when both share a node — off-node it fails with
  `Network unreachable` and corp traffic never reaches the VPN (previously it
  worked only because the scheduler happened to co-locate them).

The self-hosted GitLab exists **only in corp DNS**, which neither cluster DNS nor
the sidecar's resolver can reach — so the pod pins the hostname to its corp-VPN IP
via `hostAliases`. That mapping is employer-specific, so it lives in the local
overlay (below), not in git.

> **Lesson learned — pick the right tunnel group:** the corp VPN concentrator
> exposed several tunnel groups; only one actually passed traffic. The others
> authenticated and established, then blackholed all TCP (only public-resolver
> UDP/53 got through), which kept both this and the confluence MCP dead until the
> sidecars were pointed at the working group. Others required MFA + a client cert
> (what the desktop AnyConnect uses) or rejected these credentials outright.

## Out-of-band routing (held back, push-based)

Because the corp GitLab URL and `hostAliases` are employer-specific, this app is
**excluded** from the `mcp` app-of-apps and applied directly, like the
`networking/` apps. The committed `application.yaml` is a placeholder template
with `hostAliases: []`; the live `Application` carries the real block.

To change anything here, apply the committed manifest **merged with the live
`hostAliases`** — read the live copy first, never apply `application.yaml` as-is:

```sh
kubectl -n argo-cd get application mcp-gitlab -o yaml   # read the live hostAliases
# merge your change with that block, then apply the merged manifest
```

**Historical note.** This used to be an `application.local.yaml` overlay — a
gitignored real manifest applied directly, with `*.local.yaml` excluded by
`.gitignore`. That mechanism is retired (see the root
[`README.md`](../../README.md)) and no such file exists in this tree any more;
`.gitignore` still excludes the pattern as a safety net.

> **`hostAliases: []` in git is a scrubbed placeholder — never let Argo sync it.**
> The live Application carries a real entry mapping the upstream GitLab host to
> its VPN-side address. Without that entry the pod cannot resolve the host **at
> all** (cluster DNS has no record for it) and every call fails with
> `ENOTFOUND`. That is precisely why
> [`mcp/bootstrap.yaml`](../bootstrap.yaml) lists this file in its `exclude`
> glob: syncing it would overwrite the real entry with this empty list and take
> the server down instantly.
>
> So: apply changes to this file **out-of-band, merged with the live
> `hostAliases`** — never by removing the `exclude`. To read what is live:
> `kubectl -n argo-cd get application mcp-gitlab -o yaml`.

## Ingress timeouts — 600s, not nginx's 60s default

```yaml
nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
nginx.ingress.kubernetes.io/proxy-send-timeout: "600"
```

The upstream GitLab is reached over the openconnect tunnel, so broad queries —
`list_merge_requests` with no `project_id`, `get_merge_request` on a large MR —
routinely run past nginx's 60s default and get cut mid-flight. The client sees
only *"operation timed out"*, which reads as a dead server rather than a
truncated response. 600s is the same window the litellm ingress already uses
(see [`../../ai/litellm/README.md`](../../ai/litellm/README.md)).

## Required out-of-band secrets

Shared with the atlassian apps: `mcp-corp-config` (holds this app's
`GITLAB_API_URL` + `CIDR`), `mcp-atlassian-vpn-credentials`, and `mcp-basic-auth`
— see [`mcp/atlassian/README.md`](../atlassian/README.md). The rest are specific
to this app:

```sh
# GitLab personal access token (api scope) — read by the token-injector sidecar.
# (GITLAB_API_URL lives in the shared mcp-corp-config Secret, not here.)
kubectl -n mcp create secret generic mcp-gitlab-credentials \
  --from-literal=GITLAB_PERSONAL_ACCESS_TOKEN='<your-gitlab-pat>'

# Stateless session-sealing key (base64url, >=32 bytes). Must stay STABLE across
# restarts — rotating it forces every client to re-initialise once:
kubectl -n mcp create secret generic mcp-gitlab-stateless \
  --from-literal=OAUTH_STATELESS_SECRET="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=')"
```

## SIGSEGV crash loop after a worker-3 reboot = bit-rot in the image snapshot

Symptom (2026-08-13): the `mcp-gitlab` container exits **139 (SIGSEGV)** ~2 s
after start, **with no log output at all**, forever. Sidecars stay healthy, so
the pod sits at 2/3 and the Argo app never leaves `Progressing`.

It is not the app. Same image digest, same node, same spec had been Ready for
two days; the crash loop starts at a node reboot (here 2026-08-12 23:28 UTC) and
never recovers. What actually broke is the **on-disk copy of the image**:
`kube-worker-3` boots off an SD card (`mmcblk0`), a card flips bits silently,
and the page cache hides it until a reboot forces a cold read. containerd only
verifies digests at pull time, so nothing ever reports an error.

Confirm it by hashing the binary in the snapshot against the registry — do not
guess:

```sh
# on the node (privileged pod with / mounted at /host, or ssh)
ls -d /host/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/*/fs/usr/local/bin/node \
  | xargs sha256sum
```

Same size, same mtime, different hash than the layer in the registry = corrupt.
(To get the reference hash, pull the arm64 manifest's node layer from Docker Hub
and `sha256sum usr/local/bin/node` out of it.) A useful second check: copy the
registry's `node` binary plus the app tree into the *sidecar* and run it there —
if it starts fine on the same node, in the same netns, with the same secrets,
the only remaining variable is the container's own rootfs.

### Fix: force a re-pull, and remove every ref first

`crictl rmi <tag>` is **not enough**. containerd keeps additional refs — the
digest ref and the image-ID ref — and while any of them lives, the layer
snapshots stay referenced, GC skips them, and `ctr snapshots rm` fails with
`cannot remove snapshot with child`. A re-pull then silently reuses the corrupt
snapshot by chainID, so the crash comes back. Bumping the image tag does not
help either: the 52 MB Node base layer is shared across tags and would be reused
from the same bad copy.

```sh
kubectl cordon kube-worker-3                    # keep the replacement pod Pending
kubectl -n mcp delete pod <mcp-gitlab-pod>      # release the container snapshots

# on the node — every ref, not just the tag:
ctr -n k8s.io images rm --sync \
  docker.io/zereight050/gitlab-mcp:<tag> \
  docker.io/zereight050/gitlab-mcp@sha256:<index-digest> \
  sha256:<image-id>

kubectl uncordon kube-worker-3                  # kubelet pulls fresh, unpacks clean
```

GC drops the snapshot within seconds of the last ref going away. Verify the new
snapshot's `node` hash matches the registry before declaring victory.

Helper pods that need to land on a cordoned node must set `nodeName:` directly —
that bypasses the scheduler, so the cordon does not hold them back.

> One corrupted file is a warning about the card, not just this image. Check
> `dmesg` for I/O errors (there were none here — the corruption was silent), and
> treat a repeat as a reason to re-image the node or move its root to USB. Note
> that `ai/litellm`'s `nodeAffinity` already keeps that workload off worker-3.
