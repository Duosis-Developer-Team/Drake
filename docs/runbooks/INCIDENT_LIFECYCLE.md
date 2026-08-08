# Incidents: what opens one, what closes one, and how to run the evaluator

Drake opens incidents from its own health verdicts. There are no alert
rules to write in this sprint, and no thresholds to configure beyond the
health policy already attached to a binding
([SERVICE_HEALTH_BINDINGS.md](SERVICE_HEALTH_BINDINGS.md)).

## The state machine

```
                two trustworthy criticals
   (no incident) ────────────────────────▶ open
                                            │
                          acknowledge       │  two trustworthy healthys
                                            ▼         │
                                      acknowledged ───┤
                                            │         │
                                            ▼         ▼
                                          resolved (final)
```

`resolved` is terminal. A later failure opens a **new** incident; the old
one is never modified, so each incident describes exactly one outage.

## What opens an incident

All of these must hold, twice in a row:

- the binding is active and resolved
- the verdict is `critical`
- the result is not partial
- it was not served from last-good
- no datasource or query failure reason is present

The first qualifying evaluation only starts a streak. The second opens the
incident.

## What does **not** open one

`healthy` · `degraded` · `unknown` · `stale` · `not_configured` · partial
results · last-good responses · datasource unavailable · query timeout or
error · disabled or unresolved bindings · a re-processed evaluation.

Each of these also **breaks** the critical streak. A run of criticals
interrupted by a reading Drake could not trust is not a run.

`degraded` is recorded in health history and shown in the UI, but it opens
no incident in this sprint.

## Acknowledge

Acknowledging records that a human has seen the incident. It does **not**
close it and does not pause monitoring — the service is still down.

```
POST /v1/incidents/{id}/acknowledge
{"expected_version": 3}
```

- Needs `incident.ack` in a scope covering the service. Read access alone
  is not enough.
- The actor comes from the session. There is no field for one.
- A stale `expected_version` is refused with 409 rather than overwriting
  someone else's action.
- Repeating the same call is idempotent: it returns `changed: false` and
  writes neither a second timeline entry nor a second audit row.

There is no manual resolve. With the underlying condition unresolved, a
manually closed incident would reopen on the next cycle and the timeline
would describe clicking rather than the outage.

## Automatic resolution

Two consecutive trustworthy `healthy` evaluations. The first emits
`recovery_started`; the second resolves the incident with
`resolution_source: health_recovered`.

Anything else during a recovery — degraded, unknown, stale, partial, or a
datasource failure — emits `recovery_interrupted` and resets the streak.

**A disabled binding never resolves an incident.** Silence is not recovery.

## The API

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/incidents` | scope-filtered, cursor-paginated list |
| `GET /v1/incidents/filters` | the accepted filter vocabulary |
| `GET /v1/incidents/{id}` | detail, including `can_acknowledge` |
| `GET /v1/incidents/{id}/events` | immutable lifecycle timeline |
| `POST /v1/incidents/{id}/acknowledge` | acknowledge (CSRF + `incident.ack`) |
| `GET /v1/service-health/bindings/{id}/incidents` | recent incidents for a service |
| `GET /v1/service-health/bindings/{id}/transitions` | recorded status changes |

List filters are an allowlist: `project_id`, `environment_id`,
`environment_service_id`, `state`, `severity`, `opened_within`
(`24h`/`7d`/`30d`). An unknown value is 422, never silently ignored.

**Permissions.** Reading an incident is the same right as reading the
service's health (`environment.view`). Acknowledging needs `incident.ack`,
which the SRE/Operator, Platform Admin, Project Owner and Platform Owner
templates already carry. An incident outside your scope answers 404 — the
same answer an unknown id gives.

## Running the evaluator

Incidents are produced by a periodic runner, never as a side effect of a
GET. It is **off by default**:

```
DRAKE_INCIDENT_RUNNER_ENABLED=true
DRAKE_INCIDENT_RUNNER_INTERVAL_SECONDS=60     # floor: 30 outside local/test
DRAKE_INCIDENT_RUNNER_BATCH_SIZE=25
DRAKE_INCIDENT_RUNNER_CONCURRENCY=4
DRAKE_INCIDENT_RUNNER_LEASE_SECONDS=120
```

With the flag off, no task starts, no lease is taken and no query is
issued. **It is not enabled in the production manifest**; turning it on is
a deliberate configuration change.

Multiple replicas are safe: each cycle takes a Redis lease
(`incidents:evaluation:cycle`) with a unique token, and a replica that
loses the race does nothing at all. A crashed replica's lease expires
after `LEASE_SECONDS` rather than blocking evaluation.

One cycle at a time, for a bounded batch, at bounded concurrency. A single
binding that fails is logged and skipped; the rest of the cycle continues.

### One-shot run

For verifying a datasource change, or for a test:

```
uv run python -m drake_api.incidents.run_once --batch-size 25
```

It takes the same lease, so running it while the background runner is
active is safe. There is deliberately no "evaluate now" endpoint: a public
one would let any authenticated user drive provider load and influence when
incidents open.

## Troubleshooting

**No incidents appear for a service that is clearly down.**
Check in order: is the runner enabled; is the binding `active` and
`resolved`; does `GET /v1/service-health/bindings/{id}/health` actually say
`critical` (not `unknown` or `stale`); and has it said so twice. A single
critical only starts a streak.

**Everything went `unknown` at once.**
That is a datasource problem, not an estate problem — and by design no
incidents were opened. Check the Prometheus integration for the project.

**An incident will not resolve although the service looks fine.**
Recovery needs two consecutive trustworthy `healthy` readings. A `stale` or
partial reading in between resets the streak; the timeline records this as
`recovery_interrupted`.

**Acknowledge returns 409.**
Someone else acted on the incident. Reload the detail screen and try again
with the current version.

**Acknowledge returns 404 although the incident is visible.**
Read access without `incident.ack`. The 404 is intentional — a 403 would
confirm the incident exists to someone who may not act on it.

**The runner logs "cycle failed".**
The next tick retries and the lease has already been released. Log lines
carry no query, datasource URL or credential by design, so diagnose from
the health endpoints rather than expecting detail in the log.
