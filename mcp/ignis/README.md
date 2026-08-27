# mcp/ignis

[Ignis](https://github.com/Nystik-gh/ignis) (`docker.io/nobbe/ignis:0.8.10`) — Obsidian
running as a real web app in the browser, pointed at the **basic-memory note tree** so the
vault can be read (and edited) from a phone or laptop without a desktop Obsidian install.
Single replica on `kube-worker-3`, at `notes.whitediver.keenetic.link` behind the shared
`mcp-basic-auth` htpasswd — currently held at
[`replicas: 0`](#held-at-replicas-0--start-it-deliberately).

Ignis is a shim that reimplements the Electron APIs Obsidian uses; the image ships **no**
Obsidian code — it downloads Obsidian from the official release on first start (see
[Startup is slow the first time](#startup-is-slow-the-first-time)).

This file holds the rationale that, by repo convention, must **not** live as comments
inside `application.yaml` (see the root [`CLAUDE.md`](../../CLAUDE.md)).

## Why it lives under `mcp/`, though it is not an MCP server

It mounts `basic-memory`'s note volume, and a PVC can only be mounted from **its own
namespace**. The vault PVC is `basic-memory-data-smb` in `mcp`, so Ignis is an `mcp`
Application too. The alternative — a second static `PersistentVolume` in another namespace
pointing at the same SMB sub-directory — would mean a second object with delete power over
the live note tree, for no gain. [`mcp-gateway/`](../mcp-gateway/) is the other
non-server member of this group.

## The vault volume is *referenced*, never provisioned here

`persistence.vault` is `existingClaim: basic-memory-data-smb`. This Application creates no
PVC, no PV and no `StorageClass` for the notes — all three stay owned by
[`../basic-memory`](../basic-memory/README.md) (whose PVC also carries
`helm.sh/resource-policy: keep`). Deleting or pruning the Ignis Application therefore
cannot touch a single note; it only removes the Deployment, Service, Ingress and its own
`ignis-state` cache PVC.

**Do not add a `storageClass`/`size` to `persistence.vault`.** That turns the reference
into a provisioning request and would bind a *second*, empty volume over the mount point —
the notes would appear to have vanished.

## Why the node pin has to match basic-memory's

`basic-memory-data-smb` is `ReadWriteOnce`. Two pods may share an RWO claim only when they
are **on the same node**, so `nodeSelector: kube-worker-3` here mirrors the pin in
`basic-memory`. If that app is ever moved to another node, move this one in the same
commit or its pod stays `Pending` on the RWO restriction.

(The `smb.csi.k8s.io` driver is `attachRequired: false`, so there is no `VolumeAttachment`
and no multi-attach error to hit — the constraint is the scheduler's RWO check alone.
Concurrent access itself is safe: the class mounts with `nobrl`, and Basic Memory only
watches the tree for `*.md` changes.)

## The vault is mounted read-write, on purpose

Obsidian is not a viewer — it writes `.obsidian/workspace.json` on every layout change,
plus plugin and appearance state. A read-only mount makes it error on ordinary navigation,
so the mount is read-write and the *authentication* in front is what protects the vault.

The vault root already carries an `.obsidian/` directory (bookmarks, graph and workspace
settings from earlier desktop use); Ignis reuses it rather than creating its own.

For a strictly look-but-don't-touch instance, add `readOnly: true` under the
`persistence.vault` mount — accepting that parts of the Obsidian UI will complain.

## `notes.` needs a router entry — the Ingress alone is not enough

`*.whitediver.keenetic.link` reaches the cluster through the Keenetic router's KeenDNS
reverse proxy, and that `ip http proxy` table is **hand-maintained**. A hostname missing
from it does not fall through to ingress-nginx: the router answers `200` with its own web
UI, which reads as "the app is broken". Add the `notes` entry on the router when this
Application first syncs — see [`../../platform/nginx/README.md`](../../platform/nginx/README.md).

The LAN DNS half is automatic: `networking/keenetic-operator` syncs every Ingress host to
an `ip host` record on the router.

`WS_ORIGINS` is pinned to `https://notes.whitediver.keenetic.link` so the WebSocket
endpoint accepts that origin only. Change the host and this value together, or live sync
stops connecting.

## Secure context, and the Basic-Auth caveat

Obsidian needs browser APIs that only exist in a secure context, so Ignis must be reached
over **HTTPS** (the router's Let's Encrypt certificate) — not by hitting the node IP
directly. `ssl-redirect: "false"` matches the rest of the cluster: TLS is terminated at the
router and the hop to nginx is plain HTTP.

Ignis ships **no** authentication of its own; `mcp-basic-auth` (the same htpasswd the other
`mcp` ingresses use) is the only thing between the internet and a read-write vault. If the
live-sync WebSocket ever fails to connect while the page itself loads, suspect the browser
not replaying Basic-Auth credentials on the `wss://` upgrade — that is the known weak spot
of this auth choice, and the fix is a session-cookie proxy (oauth2-proxy / Authelia), not a
change to Ignis.

## Held at `replicas: 0` — start it deliberately

The Application is committed with `replicas: 0`. `kube-master` went unresponsive minutes
after the first sync (the router answered `502` for every cluster hostname while its own
UI stayed up), and the first-start work described below is a plausible contributor: the
`chown -R` walk hits the Samba server, which runs on `kube-master` — the CPU-saturated
control-plane node — while the vault is 1.5 GB of many small files.

That is a hypothesis, not a diagnosis; the node has its own history of instability. The
zero replica count exists so a recovering cluster does not immediately restart the walk
while it is still settling. Raise it to `1` once the node is healthy, and watch
`kube-master` CPU during the first start. If the walk is the problem, the fix is to keep
the tree out of `chown -R`'s path: mount the vault elsewhere and leave a symlink in
`/vaults` (upstream supports it, and `chown -R` does not follow symlinks).

## Startup is slow the first time

The entrypoint does three things before the server listens:

1. `chown -R` over `/vaults` and `/app/data`. On the SMB mount the uid/gid are fixed by
   mount options, so every call fails harmlessly — but it still walks the whole note tree.
2. Downloads and extracts the Obsidian release pinned by this Ignis build (~150 MB).
3. `npm install -g obsidian-headless` (optional headless-sync CLI; a warning if it fails).

Hence `startup.failureThreshold: 90` at a 10s period — 15 minutes of grace. Steps 2 and 3
need outbound internet (GitHub releases, npm).

`persistence.state` is a small `local-path` PVC holding `/app/data` (Ignis' own settings)
and `/app/obsidian-app` (the extracted Obsidian) under two `subPath`s, so a restart skips
step 2. It is node-local by design — the pod is pinned anyway — and holds nothing that
cannot be rebuilt: losing it costs one slow start.

## SMB tuning

| var | value | why |
| --- | --- | --- |
| `WRITE_COALESCE_MS` | `500` | debounces rapid writes; upstream recommends it for SMB/NFS/rclone vaults |
| `UV_THREADPOOL_SIZE` | `64` | default `4` bottlenecks file I/O over the network share |
| `AUTO_CREATE_DEFAULT` | `false` | never invent a "My Vault" in `/vaults`; the only vault is the real one |

## Image tag is pinned, not `latest`

Same reasoning as [`../basic-memory`](../basic-memory/README.md): an unpinned tag moves
under you and leaves no last-known-good. `0.8.10` was byte-identical to `latest` at the
time of pinning (`sha256:e2f62cc8…`, multi-arch with a real `linux/arm64` manifest — every
node here is a Pi). Each Ignis release pins the Obsidian version it was tested against, so
upgrading the image also moves Obsidian; do it as an explicit commit.
