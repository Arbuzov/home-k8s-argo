# graphiti-mcp

Zep Knowledge Graph MCP server backed by an in-pod Neo4j 5.26 StatefulSet.

## Required out-of-band secrets

Two Secrets in the `mcp` namespace — create both before first sync:

```sh
# Neo4j initial admin auth (consumed by the neo4j container's NEO4J_AUTH).
# Format is "neo4j/<password>" — do NOT change the username from `neo4j`,
# the image only ever creates one local account on first boot.
kubectl -n mcp create secret generic graphiti-neo4j-auth \
  --from-literal=NEO4J_AUTH="neo4j/$(openssl rand -base64 18)"

# Graphiti server: same Neo4j password (must match what's in NEO4J_AUTH
# above, minus the `neo4j/` prefix) + OpenAI API key for embeddings/LLM
kubectl -n mcp create secret generic graphiti-mcp-secrets \
  --from-literal=NEO4J_USER=neo4j \
  --from-literal=NEO4J_PASSWORD='<same-password-as-above>' \
  --from-literal=OPENAI_API_KEY='sk-...'
```

Neo4j on first boot persists the password to `/data` and ignores
`NEO4J_AUTH` afterwards. If you ever rotate the password, do it via the
`cypher-shell` `ALTER CURRENT USER` flow, then update
`graphiti-mcp-secrets`.

## Notes

- The previous manifest inlined the Neo4j auth and OpenAI key directly
  in helm values. Both have been moved to Secrets here.
