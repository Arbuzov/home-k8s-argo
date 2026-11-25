# Headlamp

Replaces the retired Kubernetes Dashboard. Access:
https://dev.whitediver.keenetic.link/headlamp/

The existing Google oauth2-proxy in namespace mcp authenticates the
browser, using its current allowed-email list and shared domain cookie.
No new Google OAuth client or credentials are required. Headlamp uses a
dedicated read-only ServiceAccount (view + selected cluster metadata);
Google identifies the user but does not authenticate directly to the
Kubernetes API server. Helm operations, writes and Secret reads are
not granted.

The API server currently has no Google OIDC configuration. Since Flannel
does not itself enforce network policies, a simple trusted-header ingress
would be insufficient. Headlamp binds to loopback on port 4467. Its Service
reaches the nginx sidecar at port 4466, which independently checks the
Google session against oauth2-proxy before forwarding and overwrites
identity headers. The rotating API token is mounted only into Headlamp.
The existing ingress also enforces Google SSO. Do not expose port 4467
or enable cluster-admin for this shared ServiceAccount.

Both the chart and sidecar image use exact release versions.
