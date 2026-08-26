# Conventions for AI agents

GitOps repo of Argo CD `Application` manifests for the home-lab cluster.
Read [`README.md`](README.md) first for layout, deploy models, and
secrets handling.

## Manifests carry no comments — rationale goes in README

Keep `*.yaml` manifests (`application*.yaml`, `project.yaml`,
`bootstrap.yaml`) as pure declarative config. Do **not** add explanatory
`#` comments to them.

Any rationale — why a value is set, incident history, tuning notes,
trade-offs, "don't revert this" warnings — belongs in the service's
sibling `README.md` (`<group>/<service>/README.md`), not in a YAML
comment. Create the README if it doesn't exist.

When you change a manifest for a non-obvious reason, write that reason in
the README. When you encounter existing comments in a manifest, move
their content into the README and delete the comments.

**Which README:** a `<group>/<service>/*.yaml` manifest → that service's
`<group>/<service>/README.md`. A group-level `project.yaml` / `bootstrap.yaml`
→ the group `<group>/README.md`. If a manifest has no sibling README (e.g. a
`db/` sub-manifest), fold the rationale into the parent service's README.

**Not covered by this rule** (these stay — they are program source, not
manifest rationale): comments *inside* an embedded script carried in a block
scalar (`command: |` / `args: |` shell or Python), and non-comment `#` tokens
in an embedded language (e.g. Lua's `#length` operator).

Self-check before committing — list stray YAML comments, both whole-line and
trailing (`memory: 1Gi   # was 256Mi`, which the whole-line pattern misses):

```sh
git ls-files '*.yaml' '*.yml' | grep -v secret \
  | xargs grep -nE '(^[[:space:]]*#|[^[:space:]][[:space:]]+#[[:space:]])' -- 2>/dev/null
```

Every hit is either a violation or one of the carve-outs above — read each one,
don't assume. The test is **where the `#` sits**, not which file it is in: inside
an embedded `command:`/`args:` block scalar it is program source and stays;
anywhere else in the YAML it is manifest rationale and belongs in a README.
(At the time of writing the only hits were script comments in
`ai/litellm/db/nim-sync-cronjob.yaml`, `observability/claude-status/configmap.yaml`,
`ai/n8n/application.yaml` and `observability/influxdb/databases/cronjob.yaml` —
useful as a sanity check, not as a closed list.)
