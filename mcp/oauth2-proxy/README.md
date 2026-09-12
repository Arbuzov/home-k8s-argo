# oauth2-proxy

Google login in front of hosts that ship no authentication of their own. It
currently protects `notes.*` (`mcp/ignis`) and `homepage.*` (`apps/homepage`).
nginx asks this proxy about every request through `auth_request` and sends the
browser to Google when there is no session.

Oathkeeper (`platform/oathkeeper`) cannot fill this role. It validates a session
that already exists, but nothing on its side sends the browser to Google or
handles the callback.

`application.yaml` is kept comment-free by convention. The rationale lives here.

## Secret

`config.existingSecret: oauth2-proxy` (namespace `mcp`), with keys `client-id`,
`client-secret` and `cookie-secret`. It is created out-of-band because the repo
is public. The Google OAuth client is the same one Grafana uses (same
`client_secret`).

## One login for every `*.whitediver.keenetic.link` host

- `cookie-domain` and `whitelist-domain` are `.whitediver.keenetic.link`, not a
  single host. The session cookie is therefore sent to every subdomain, and
  after login `rd=` may point back to any of them.
- `redirect-url` stays `https://notes.whitediver.keenetic.link/oauth2/callback`.
  That is the redirect URI registered on the Google client, and the proxy's own
  ingress serves `/oauth2` on the `notes` host only. Putting another host behind
  the proxy needs no change in the Google Console.
- Browsers accept a cookie on `.whitediver.keenetic.link` because `keenetic.link`
  (not `*.keenetic.link`) is on the Public Suffix List. That makes
  `whitediver.keenetic.link` the registrable domain. Checked 2026-09-13.
- Trade-off: the session cookie also reaches hosts that are **not** behind the
  proxy (n8n, litellm, vikunja, …). It is encrypted with `cookie-secret`, but it
  can be replayed. That is acceptable for a single-user lab where every one of
  those hosts is ours. Do not add a host run by someone else under this domain.

## Who gets in

- `authenticatedEmailsFile.restricted_access` holds an explicit allow-list, not
  a domain, so a new `whitediver.com` account gets no access by itself. The list
  covers **every** protected host at once.
- `email-domain: "*"` is required together with the list. Without it
  oauth2-proxy filters by domain first and never reaches the list.
- The key really is `restricted_access`. The chart has a similar-looking
  `restrictedUserAccessKey`, but that is the *name* of the ConfigMap key, not its
  content. With that typo the ConfigMap is not rendered, the pod stays in
  `ContainerCreating` with `FailedMount`, and every protected ingress returns 500
  because nginx has nowhere to send the `auth_request`.

## Other flags

- `reverse-proxy: "true"`: trust `X-Real-IP` / `X-Forwarded-{Proto,Host,Uri}`
  from the nginx ingress, for the client IP and for redirect selection. The
  401 → login redirect does not depend on it: `/oauth2/auth` always answers
  202 or 401, and nginx turns the 401 into the `auth-signin` redirect for the
  browser.
- Ingress `ssl-redirect: "false"`: the same as the other ingresses. TLS is
  terminated at the router, so there is no reason to redirect to https
  in-cluster.

## Putting another host behind it

Add two annotations to that host's Ingress:

```yaml
nginx.ingress.kubernetes.io/auth-url: "http://oauth2-proxy.mcp.svc.cluster.local/oauth2/auth"
nginx.ingress.kubernetes.io/auth-signin: "https://notes.whitediver.keenetic.link/oauth2/start?rd=https://$host$escaped_request_uri"
```

- `auth-url` goes to the in-cluster Service, so each check stays inside the
  cluster instead of going out through the router and back.
- `auth-signin` is the public URL, because the browser follows it. `rd=` must be
  **absolute** (`https://$host…`) on any host other than `notes`. The relative
  `rd=$escaped_request_uri` that `mcp/ignis` uses only works because it is on
  the callback host itself.
- The wider `cookie-domain` / `whitelist-domain` must be live before the new
  host's annotations are. Otherwise the browser loops through Google, because
  the cookie never reaches the new host. When both changes ship in one commit,
  this converges within one sync.
