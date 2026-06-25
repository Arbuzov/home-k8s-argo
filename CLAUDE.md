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

Example already converted: `observability/influxdb/` — the OOM/tuning
rationale lives in [`observability/influxdb/README.md`](observability/influxdb/README.md),
and `application.yaml` is comment-free.
