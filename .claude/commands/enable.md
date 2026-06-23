---
description: Enable an app-of-apps service via pure GitOps, no manual kubectl
argument-hint: <service>  e.g. jellyfin or media/jellyfin
---

Enable the service **$ARGUMENTS** the GitOps way. Only action is a git commit + push;
Argo CD creates and syncs the child Application on its own. No manual `kubectl apply`.

Steps:

1. Locate `<group>/<service>/`. If `<dir>/application.yaml.disabled` exists, this is the
   normal path → step 2. If the live manifest is already `application.yaml` AND the service
   is **not** listed in any `bootstrap.yaml` `exclude` glob, it's already on — stop.
2. `git mv <dir>/application.yaml.disabled <dir>/application.yaml` (+ every
   `application-*.yaml.disabled` variant). This brings it back into the app-of-apps
   `include` glob; Argo CD picks it up on the next poll. No bootstrap re-apply needed.
3. **Legacy case** — if instead the service is held back by the group's `bootstrap.yaml`
   `exclude` glob (the old mechanism, e.g. opds-shelf): remove its entry from that glob so
   it's consistent with the rename scheme. Warn the user that this ONE edit to
   `bootstrap.yaml` requires a single `kubectl apply -f <group>/bootstrap.yaml` (the
   app-of-apps never manages itself) — the only manual step, and only when migrating off the
   legacy `exclude`. Going forward the service toggles purely by rename.
4. **Pre-flight secrets check** (read-only): if `<dir>/README.md` lists out-of-band Secrets,
   verify they exist (`kubectl get secret -n <ns>`); a sync will hang without them. Report
   any missing ones — the user creates them out-of-band (this repo commits no secrets).
5. Commit (`<group>/<service>: enable — rename manifest back into app-of-apps include glob`)
   and push to `main`.
6. Report: Argo CD will create + sync the child on its next poll. If the service had data on
   a `Retain` PV that went `Released` during a prior disable, the fresh PVC won't auto-bind —
   clear the old PV's `claimRef` to reattach the data (see media/README.md).

No manual workload apply/sync — git is the only delivery path.

Repo convention: manifests stay comment-free — any rationale you'd write as a
YAML comment goes in the service's `<group>/<service>/README.md` instead (see
media/opds-shelf for the pattern).
