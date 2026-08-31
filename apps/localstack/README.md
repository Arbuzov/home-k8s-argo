# localstack

A local AWS cloud stack for testing against S3/SQS/Lambda/etc. without a real
AWS account. Deployed from the official chart at `https://helm.localstack.cloud`
(chart `localstack`, pinned to `0.7.0`).

This file holds the rationale that, by repo convention, must **not** live as
comments inside `application.yaml` (see the root `CLAUDE.md`).

## `image.repository: localstack/localstack`

The chart's default image is `localstack/localstack-pro`, which requires a
`LOCALSTACK_AUTH_TOKEN` (a paid license) to start at all. This repo carries no
credentials, so the image is switched to the free community edition instead —
no auth token, no Secret needed.

## No ingress, no persistence

Left at chart defaults: `service.type: NodePort` (edge service on node port
`31566`, the standard way to point an AWS SDK/CLI at an in-cluster
LocalStack), and `persistence.enabled: false` — state resets on pod restart,
which is normal for a throwaway test double.
