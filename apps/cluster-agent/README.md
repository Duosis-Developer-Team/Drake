# Drake Cluster Agent

Read-only Kubernetes inventory agent. Sprint 0 ships the foundation only:
typed config, structured logging with redaction, health/liveness, graceful
shutdown, and the outbound transport/enrollment/collector interfaces. There is
**no Kubernetes client and no cluster connection** in this sprint.

## Security boundaries (v1, non-negotiable)

These are enforced in code and tests, not just documented:

1. **Read-only.** The agent observes; it never mutates cluster state.
2. **No Secrets.** The agent never reads Kubernetes `Secret` resources.
3. **No ConfigMap data.** Metadata at most; `data`/`binaryData` are never collected.
4. **No `exec`/`attach`/`portforward`.** These verbs are never requested or used.
5. **No Kubernetes write verbs.** `create`/`update`/`patch`/`delete` are never requested.
6. **No wildcard RBAC.** Resources and verbs are always enumerated explicitly.
7. **No inbound control port.** The only listener is a loopback-bound liveness
   probe endpoint; commands can never be pushed to the agent.
8. **Outbound-only connectivity.** The agent dials the Drake API (TLS);
   nothing dials the agent.
9. **No secret/token logging.** All log output passes redaction.

The collector registry rejects any collector that declares a forbidden
resource kind (see `internal/collector`), so a future regression fails at
startup and in unit tests rather than at review time.

## Development

```bash
go build ./...
go vet ./...
go test ./...
```

Configuration is environment-based (`DRAKE_AGENT_*`), see `internal/config`.
The API endpoint must be `https://`; plaintext is tolerated only for
`127.0.0.1`/`localhost` development targets.
