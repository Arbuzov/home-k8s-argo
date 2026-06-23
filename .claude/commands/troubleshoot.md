---
description: Diagnose an Argo CD service read-only; fixes go through GitOps only
argument-hint: <service>  e.g. jellyfin or media/jellyfin
---

Troubleshoot **$ARGUMENTS**. Diagnosis is read-only. Any fix you propose must be a git edit
the user commits + pushes (Argo reconciles it) — never a manual `kubectl apply/edit/scale`
or `argocd app sync`. Use the kubernetes MCP / read-only `kubectl get|describe|logs`.

Locate `<group>/<service>/` and its namespace from the manifest, then work down the chain
and stop at the first broken link:

1. **Argo CD app** — `kubectl get application -n argo-cd <service>`: sync status + health.
   `OutOfSync`/`Missing` often means the manifest was never rendered → is it renamed
   `*.disabled` or sitting in a `bootstrap.yaml` `exclude` glob? `Degraded` → go deeper.
2. **Pods** — `kubectl get pods -n <ns>` + recent `kubectl get events`. For any not Running:
   `describe` it. Triage the usual: `ImagePullBackOff` (tag/registry), `CreateContainer
   ConfigError`/`CrashLoopBackOff` from a missing **Secret** (this repo keeps secrets
   out-of-band — `kubectl get secret -n <ns>`, cross-check the service README), `Pending`
   from an unbound **PVC** (`kubectl get pvc,pv -n <ns>` — a `Retain` PV left `Released` by a
   prior disable needs its `claimRef` cleared to rebind).
3. **Logs** — `kubectl logs -n <ns> <pod> [--previous]` for crash loops.
4. **Reachability** (if it serves traffic) — Service endpoints populated? Ingress host/TLS?
5. Storage on CIFS/SMB is a known foot-gun here: Postgres/WAL corrupts on SMB — if it's a DB
   on a CIFS-backed `local-path`, that's likely the cause (see ai/n8n commits).

Report: the broken link, the evidence (the command output that shows it), and the fix as a
**git change** (which file, what edit) plus whether it needs an out-of-band Secret the user
must create. Do not mutate the cluster.

Repo convention: manifests stay comment-free — when your proposed fix adds
rationale, put it in the service's `<group>/<service>/README.md`, not as a YAML
comment (see media/opds-shelf for the pattern).
