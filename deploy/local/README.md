# Local development stack

PostgreSQL 16 + Redis 7 for local development and integration tests.
Everything binds to `127.0.0.1` only. The credentials in the Compose file are
deliberate local-only development values.

| Command | Effect on data |
|---|---|
| `make up` | starts the stack and waits for health checks |
| `make down` | stops and removes containers, **keeps volumes/data** |
| `make integration-test` | runs integration-marked tests against this stack |
| `make destroy-local-data` | **DESTRUCTIVE**: removes containers *and* volumes |

`destroy-local-data` refuses to run when the environment looks non-local
(`DRAKE_ENV` set to prod/production/test, or a Kubernetes service environment
is detected) and is never chained into any other target.
