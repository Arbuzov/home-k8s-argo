# mcp/basic-memory

Basic Memory MCP server (`ghcr.io/basicmachines-co/basic-memory:latest`),
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

## Semantic search env is pinned, not defaulted

`BASIC_MEMORY_SEMANTIC_*` mirror the image defaults (enabled, local FastEmbed
`bge-small-en-v1.5`, no API key) but are pinned here so behaviour stays
deterministic if the `latest` image ever changes its defaults. The embedding
model cache is **not** persisted, so on each pod start FastEmbed re-downloads
the model from HuggingFace and re-embeds the rebuilt SQLite index; if HF is
unreachable at start, search silently falls back to text until the model is
available.

## SMB volume + StorageClass naming

The data volume and its `StorageClass` are both managed by this one
Application (no out-of-band files). app-template prefixes every object name
with the release name, so the class is `basic-memory-smb` and the PVC is
`basic-memory-data-smb`. The CSI `subDir` is derived from the PVC
namespace+name (`pvc-mcp-basic-memory-data-smb`), so the share folder — and
thus the data — is independent of the class name; renaming the class does not
orphan the notes. Only the markdown note tree lives here; the SQLite index is
rebuilt off-volume on each start. `uid/gid=1000` matches the image's appuser.
