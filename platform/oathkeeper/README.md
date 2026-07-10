# oathkeeper

[Ory Oathkeeper](https://www.ory.sh/oathkeeper/) — an identity & access proxy /
decision API for the ingress layer. Chart: `oathkeeper` from
`https://k8s.ory.sh/helm/charts` (that URL 308-redirects to `k8s.ory.com`;
Helm/Argo follow it), pinned `0.62.1` (appVersion `v26.2.0`).

## Status: disabled

Scaffolded but **not** deployed. The manifest is `application.yaml.disabled`,
so it falls outside the platform app-of-apps `include` glob
(`*/application*.yaml`) and Argo CD ignores it.

Bare Oathkeeper enforces nothing: the committed `helm.values` ship a minimal,
Healthy-on-start config (anonymous + noop authenticators, allow/deny
authorizers, noop mutator, **empty** `access_rules.repositories`) and `maester`
disabled (no `Rule` CRD). It proxies nothing until you add access rules — that
is the whole point of enabling it.

## Enabling

The chart repo and the `oathkeeper` namespace are already registered in
[`../project.yaml`](../project.yaml), so enabling is a pure rename — no
`bootstrap.yaml` re-apply:

```sh
git mv platform/oathkeeper/application.yaml.disabled platform/oathkeeper/application.yaml
# add real rules first — see below
git commit -am "platform/oathkeeper: enable" && git push
```

The platform app-of-apps runs `automated` sync, so the push alone rolls it out.

## Before you enable

1. **Add access rules.** Either set `oathkeeper.accessRules` inline (rendered to
   a ConfigMap) or point `oathkeeper.config.access_rules.repositories` at a
   rules source, or re-enable `maester` to drive rules via the `Rule` CRD.
2. **Pick a real authenticator.** The scaffold is anonymous/allow — swap in
   `jwt` / `oauth2_introspection` / `cookie_session` against your IdP (Google
   OIDC is already used by `argo-cd` and `apps/vikunja`).
3. **Wire the ingress.** No ingress is defined yet. To front other services,
   route them through Oathkeeper's proxy (`:4455`) or use the decision API
   (`:4456`) via the nginx `auth-request` annotation.
4. **Re-validate the values** against the pinned chart before first sync — the
   config keys above are a minimal starting point, not a verified production
   config.

No out-of-band Secrets are required by the scaffold. Real authenticators
(JWKS URLs, client secrets) will add some — document them here when you wire
them.
