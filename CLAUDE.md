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

Self-check before committing — list stray whole-line YAML comments:

```sh
git ls-files '*.yaml' '*.yml' | grep -v secret \
  | xargs grep -nE '^[[:space:]]*#' -- 2>/dev/null
```

Examples already converted: `observability/influxdb/`, `ai/litellm/` — the
rationale lives in the sibling `README.md` and `application.yaml` is comment-free.
