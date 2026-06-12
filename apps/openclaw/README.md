# openclaw

## Required out-of-band secret

The `main` container reads its env from `openclaw-env-secret`. Create it
before first sync — the chart expects whatever variables your specific
openclaw install needs (refer to the upstream chart's `env` docs):

```sh
kubectl -n openclaw create secret generic openclaw-env-secret \
  --from-literal=KEY=VALUE \
  --from-literal=ANOTHER_KEY=ANOTHER_VALUE
```
