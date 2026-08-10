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

The full suite also runs on every push to main, on the nightly schedule,
and on manual dispatch. Post-merge full regression on main is what makes
per-PR narrowing acceptable — it is not optional, and its concurrency group
deliberately does **not** cancel.

`ci gate` is the single required check. It aggregates every job, treating
`skipped` as a pass and `failure`/`cancelled` as a fail, so conditioning a
job can never silently drop a gate and branch protection does not need
editing when the matrix changes.

## Local policy

Do not run the whole repository after every small edit. In order:

1. Work out what you changed.
2. Run the narrowest relevant tests.
3. Run the matching lint/typecheck.
4. Widen if the change is shared or risky.
5. Before committing, run what the impact policy would select:
   `python3 scripts/ci_impact.py --base origin/main --head HEAD`
6. Watch the PR run; fix what you broke rather than re-running it.
7. Stop every server and cluster you started.

Run the **full** suite regardless for: auth/RBAC/security boundaries,
shared contracts, database or migration changes, dependency or lockfile
changes, CI or test-infrastructure changes, broad refactors, release
candidates, and anything whose impact you cannot state.

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

`integration` is the slowest job at roughly 6m30s, nearly all of it 759
integration tests against one shared database. Parallelising needs
per-worker database isolation in `conftest.py` — a real change with real
flakiness risk, deliberately not bundled into the CI restructuring.
