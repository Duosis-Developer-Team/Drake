# Sprint 12A.2b — production-readiness closeout

Branch `feat/sprint-12a2b-onboarding-production-readiness`, from
`ae3233db1c61e9fbf649d5035a52a0aa906bb2da` (`dev`). Migration head `0020`,
unchanged — no migration in this slice.

## What this closed

Sprint 12A ships an onboarding control plane that **cannot write to a
repository**. The risk left open at the end of 12A.2a was that it did not
enforce that: `RecordingProvider` was the startup default, so a
misconfiguration that turned the GitOps flags on would have produced
`active` pull requests that do not exist.

### The provider boundary

| Environment | PR flag | Worker flag | Provider | Result |
| --- | --- | --- | --- | --- |
| production | off | off | none | starts; GitOps unreachable |
| production | **on** | any | none | **refuses to start** |
| production | off | **on** | none | **refuses to start** |
| production | on + on | on | none | **refuses to start** |
| local/test | on | on | recording double, wired explicitly | worker runs |
| local/test | off | any | recording double, constructed | 0 provider calls |

Two guards, independently sufficient:

1. `Settings.validate_runtime_security` refuses either flag outside
   local/test, naming both so the message is actionable.
2. `create_app` constructs no provider outside local/test, so a caller past
   the flags still has nothing to call — and no worker, because the worker
   requires a provider.

The refusal is at startup, not at the first request: a worker running
against a fake, and requests accepted but never deliverable, are both
half-enabled states that look like working features.

No new public API field. `gitops_pr_enabled` on
`GET /v1/onboarding/github/status` is `false` in production by
construction, and the screen's existing sentence is the truth.

## Verification

Everything below ran without network access, a real token, or a repository
write. Provider contact happens only where a test passes a
`RecordingProvider` in explicitly.

| Gate | Result |
| --- | --- |
| Backend, single process, unfiltered | 1373 passed |
| Backend `-m "not integration"` (CI) | 640 passed |
| Backend `-m integration` (CI) | 746 passed |
| Web unit | 211 passed |
| Contracts | 63 passed |
| Playwright E2E | 45 passed |
| Go agent | 8 packages ok |
| ruff format + check (repo root) | clean |
| mypy strict (`apps/api/src`, `apps/worker/src`) | clean |
| Migration head | `0020`, single head |
| Secret scan (history + tree + canary) | no leaks |
| Browser provider guard | clean |

New in this slice:

- **Production boundary** — 8 tests over the configuration matrix above,
  asserted against real `Settings` validation and a real `create_app`.
- **Release-candidate closeout** — the authoritative path end to end
  (projection → session → analyse → plan → approve → apply → catalog) with
  the write path off: one project, one receipt, one apply audit, zero
  GitOps requests. Plus the manifest draft with zero provider calls, the
  disabled write path refusing with `gitops_disabled` and minting no token,
  the dispatcher reachable only through an injected double, and a
  re-assertion that no scope or gate guarantee was relaxed on the way.

## The single-process test debt, and what it actually was

Carried from 12A.2a as "two FakeRedis isolation failures". That diagnosis
was wrong, and the correction matters more than the fix.

`test_service_health_read_path_unit` stamps its fixtures from a module
constant captured at **import**. `current_health` reads the wall clock
itself. In a short run the gap is nothing; in a full single-process run the
module is imported when collection starts and executes six minutes later,
by which point the fixture samples are old enough to be judged
`telemetry_stale` — and a stale verdict is deliberately not promoted to
last-good, so the two tests that read the cache directly found it empty.

Nothing leaked between tests, and no Redis was involved. It was a clock
dependency that only a long run could expose.

Fixed in the fixture: an autouse fixture re-stamps the module's clock
before each test, so a test's data and its notion of "now" always come from
the same instant. No production change, no assertion weakened, no test
skipped. The unfiltered single-process suite now passes.

## Boundaries held

No real GitHub provider or write. No branch, commit or pull request. No
GitHub API call to create anything. `github_gitops_pr_enabled` and
`gitops_worker_enabled` off. Datalake `manual_env_review` gate open and
untouched. No migration; head `0020`. No deploy, image publish, Helm,
Kubernetes apply or promotion. Sprint 12B not started. No test deleted,
skipped, or loosened.
