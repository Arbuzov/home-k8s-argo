---
description: Disable an app-of-apps service via pure GitOps (data-safe), no manual kubectl
argument-hint: <service>  e.g. jellyfin or media/jellyfin
---

Disable the service **$ARGUMENTS** the GitOps way. Rules: no `kubectl apply/delete/scale`,
no manual sync — the only action is a git commit + push that Argo CD reconciles on its own.
Data must survive.

Mechanism (why rename, not the `exclude` glob): the app-of-apps `include` glob is
`*/application*.yaml`. Renaming the child manifest out of that glob drops it from the
rendered set on the next git poll, so Argo CD prunes the workload automatically — **without**
re-applying `bootstrap.yaml` (which the app-of-apps never manages). Editing the `exclude`
glob would need a manual re-apply, so don't.

Steps:

1. Locate the service dir: `<group>/<service>/` where group ∈ {ai, apps, mcp, media,
   observability, platform}. If `$ARGUMENTS` already has a group prefix, use it. There must
   be a live `<dir>/application*.yaml`. If it's already `*.disabled`, it's off — stop.
2. Confirm the group is pull-based (one of the six above). Push-based groups
   (networking, storage) aren't watched by Argo — renaming won't prune them; warn and stop.
3. **Data-safety gate** — read-only check, this is verification not a mutation: for each PVC
   the manifest mounts, run `kubectl get pv -o ...` and confirm the bound PV is
   `persistentVolumeReclaimPolicy: Retain`. If any is `Delete`, STOP and report — pruning
   would destroy data. (Media volumes are all Retain by design; see media/README.md.)
4. `git mv <dir>/application.yaml <dir>/application.yaml.disabled` — and likewise every
   `application-*.yaml` variant in that dir.
5. Commit (`<group>/<service>: disable — rename manifest out of app-of-apps include glob`)
   and push to `main`.
6. Report: Argo CD will prune the child Application on its next poll; the workload + PVC are
   removed but every PV is `Retain`, so the underlying data is kept (`Released`, not deleted).
   Re-enable later with `/enable $ARGUMENTS`.

Do not touch the `exclude` glob, do not run any apply/delete, do not delete PVs.

Repo convention: manifests stay comment-free — any rationale you'd write as a
YAML comment goes in the service's `<group>/<service>/README.md` instead (see
media/opds-shelf for the pattern).
