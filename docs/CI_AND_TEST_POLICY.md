# CI and test policy

CI runs the tests a change actually needs. This document says how that is
decided, what can never be narrowed, and what to run locally before a
commit.

The decision lives in [`scripts/ci_impact.py`](../scripts/ci_impact.py) and
is itself tested in
[`apps/api/tests/test_ci_impact.py`](../apps/api/tests/test_ci_impact.py).
Change the policy there, not by hand in a workflow condition.

## The two rules that make narrowing safe

**An unrecognised path runs everything.** The classifier is an allowlist.
Anything it does not recognise sets `unknown`, which turns the full suite
back on — as does an empty or failed diff, a lockfile, CI/test
infrastructure, and anything security-shaped. A classifier that defaults to
"probably fine" is how a change ships without the one gate that would have
caught it.

**The security gates are not selectable.** Secret scanning and dependency
scanning run on every change, including a one-word docs edit, because "this
diff is only docs" is a claim the diff makes about itself. `ci gate` fails
if either one did not actually succeed.

## What runs for what

| Change | Jobs |
| --- | --- |
| docs / Markdown only | secret scan, dependency scan |
| frontend | contracts, web, e2e |
| backend module | contracts, python, integration |
| database / migration | python, integration |
| cluster agent (Go) | go agent, chart, k3d runtime |
| chart | chart, k3d runtime |
| internal listener / PKI | chart, k3d runtime, **and the full suite** (also security-sensitive) |
| shared contracts, models | contracts, web, python, integration, e2e |
| auth / RBAC / settings | everything |
| lockfile, CI or test infrastructure | everything |
| unrecognised path | everything |

**main is classified the same way a PR is.** It used to run the full suite
unconditionally, so a docs-only merge cost seven minutes and gave back most
of what the per-PR narrowing had just won. Full regression is preserved
where it earns its cost:

- the nightly schedule (03:00 UTC)
- manual `workflow_dispatch`
- any high-risk classification — security-sensitive, shared contract,
  migration, dependency/lockfile, CI or test infrastructure, unknown
- a push whose before-SHA is missing or unreachable (force push, new
  branch), which fails safe to full

main's concurrency group deliberately does **not** cancel: every merged
commit keeps its own verification.

**Before promoting a commit to production**, confirm it has a full-suite
result — the nightly run that covered it, or a `workflow_dispatch` on that
SHA. A narrow main run proves the change, not the whole system, and
production promotion is the one place that difference matters.

`ci gate` is the single required check. It aggregates every job, treating
`skipped` as a pass and `failure`/`cancelled` as a fail, so conditioning a
job can never silently drop a gate and branch protection does not need
editing when the matrix changes.

## Integration test selection

`integration` used to run all 760 tests for any backend edit, at ~6m30s.
The suites are grouped by the surface they exercise — `auth_rbac`,
`projects_catalog`, `clusters_inventory`, `agents_enrollment`,
`agents_ingest`, `telemetry`, `integrations`, `database_shared`,
`api_contract_shared` — and a backend module selects only the groups it can
affect. A telemetry change runs 10 suites in ~53s instead of 46 in ~5m30s.

Narrowing happens **only** when every changed path is one the mapping has
reasoned about. One unmapped backend file (`db.py`, a new module, the
session layer) puts the whole suite back, and so does anything already
classified full. A test asserts that every suite pytest collects belongs to
exactly one group, so adding a suite without placing it fails loudly rather
than dropping it from narrow runs.

## Local policy

Do not run the whole repository after every small edit. In order:

```
scripts/verify_local.sh --list    # what CI would run for this diff
scripts/verify_local.sh           # actually run it
scripts/ci_job.sh <job>           # one job, exactly as CI runs it
```

`verify_local.sh` and the workflow both call `scripts/ci_job.sh`, and both
ask `ci_impact.py` what is relevant. That is the point: "it passed locally"
and "it will pass in CI" should not be two different claims. It reads the
working tree as well as the committed diff, so it is useful before you
commit.

It cannot run `e2e` or `k3d-runtime` — those need a disposable cluster, a
browser and service containers — and it says so rather than implying
coverage it does not have.

**CI confirms a change. CI is not where a change is first debugged.** Before
pushing: run the selected jobs locally, `bash -n` any shell you touched,
parse any workflow YAML you touched, and re-run the lifecycle regression if
you touched process handling.

Run the **full** suite regardless for: auth/RBAC/security boundaries,
shared contracts, database or migration changes, dependency or lockfile
changes, CI or test-infrastructure changes, broad refactors, release
candidates, and anything whose impact you cannot state.

## When CI fails

Classify before reacting:

| Class | Action |
| --- | --- |
| Deterministic defect | Reproduce locally first, fix, run the local checks, **one** new push to the **same** PR |
| Transient (registry 500, runner, network) | Re-run **only the failed job** on the same SHA, once. No code change, no empty commit, no new PR |
| Known flake | Fix the root cause; record it if it cannot be fixed now |
| Superseded run | Ignore it — a newer commit cancelled it |
| Unknown | Diagnose from logs and local parity. Do not push a guess |

Never: an empty retry commit, a new PR per fix, or re-running the whole
workflow to clear one transient job.

## One task, one PR

A fixable CI problem in a PR is fixed **in that PR**. A separate PR is for a
genuinely separate scope, a defect found on main after merge, or a change
needing its own security review. Do not open a throwaway PR to demonstrate
CI behaviour — prove it with tests and fixtures.

## Waiting for CI

CI runs asynchronously. After a push: enable auto-merge, report the PR and
SHA, and continue working. Watching a CI page is not work.

```
gh pr merge <n> --auto --merge
```

Waiting for a green result is required only for: production deployment,
release candidates, database migrations, security-boundary changes, work
that genuinely depends on the previous merge, or when the user asks for the
merged result.

## Process lifecycle

Any script that starts a child must source
[`scripts/lib/process_lifecycle.sh`](../scripts/lib/process_lifecycle.sh)
and call `lifecycle_install_traps`. Register children with
`lifecycle_track` and extra teardown with `lifecycle_on_cleanup`.

It kills process **trees**, not pids. This matters: a cleanup that ran
`kill "$WEB_PID"` on the subshell around `pnpm` left the `next-server`
grandchild holding port 13100 for two days. Cleanup is idempotent, hooks
are eval'd whole (so a multi-line guard keeps its control flow), and a
failing hook cannot overwrite the test's own exit status.

`scripts/process_lifecycle_regression.sh` proves it: children die on
success, failure, timeout and Ctrl+C; an untracked process with an
identical command line survives; interrupts report 130 rather than a silent
0.

**Never use a fixed port for a test server.** Bind port 0 and let the OS
choose. A hard-coded liveness port made two inventory specs fail
intermittently for two days, and the failure looked like a product bug
(`not_configured`) rather than a bind error.

## Known cost

A full `integration` run is still ~6m30s: 760 tests against one shared
database. Narrow selection avoids paying it on most PRs, but a shared or
security-sensitive change still does. Reducing it further needs per-worker
database isolation in `conftest.py` — a real change with real flakiness
risk, deliberately not bundled into CI work.

The integration job refuses to run when `DRAKE_IT_DATABASE_URL` /
`DRAKE_IT_REDIS_URL` are unset: those tests skip themselves, so a missing
variable would otherwise produce a green job that tested nothing.
