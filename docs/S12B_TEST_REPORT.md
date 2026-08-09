# Sprint 12B — the real GitHub provider

Branch `feat/sprint-12b-real-github-provider`, from
`391b5a8c29308016bb4aabbd5585e0cf32a687e7` (`dev`). Migration head `0020`,
unchanged.

**No real GitHub organisation was contacted.** No branch, commit or pull
request exists anywhere as a result of this work. Every provider behaviour
below is proved against a stateful in-process fake of the GitHub write API
that records which mutations were actually applied.

## What shipped

`GitHubPullRequestProvider` — the real implementation. Production gets it
when a GitHub App is configured, and never gets the recording double. Both
GitOps flags stay off by default: shipping the code and turning it on are
separate decisions.

The contract, in one sentence: **the same session, at the same base commit,
with the same content produces at most one branch, one commit and one pull
request** — however many attempts, whatever the network does.

That is why every step reads before it writes. Writes never go through the
read client's retry loop; a lost response is `github_write_ambiguous` and
the next attempt reconciles by looking.

## Create-or-reuse, proved

| Scenario | Applied branches / commits / pulls |
| --- | --- |
| Fresh proposal | 1 / 1 / 1 |
| Same proposal again | 1 / 1 / 1 (reused) |
| Branch create response lost after it landed | 1 / 1 / 1 |
| Commit response lost after it landed | 1 / 1 / 1 |
| Pull-request response lost after it landed | 1 / 1 / 1 |
| Three sequential passes | 1 / 1 / 1 |
| Pull-request create answers 422 | 1 / 1 / 1 (found, reused) |
| Existing Drake branch, matching content | 0 / 0 / 1 |
| Base branch moved | 0 / 0 / 0 |
| Foreign content on a `drake/` branch | 0 / 0 / 0 |
| Repository id mismatch | 0 / 0 / 0 |
| Archived repository | 0 / 0 / 0 |
| Missing `contents: write` | 0 / 0 / 0 |
| 401 / 403 / 429 / 500 / 503 | 0 / 0 / 0 |
| Path outside `.drake/project.yaml` | 0 / 0 / 0 |
| Draft failing its safety check | 0 / 0 / 0, and zero HTTP calls |

401/403 are terminal — retrying cannot grant a permission. 429 and 5xx are
retryable and inherit the existing lease, timeout and `MAX_ATTEMPTS`
bounds unchanged.

## Production wiring

| Environment | GitHub App | Both GitOps flags | Result |
| --- | --- | --- | --- |
| production | off | off | no provider, no worker |
| production | on | off | real provider available, no write worker |
| production | on | both on | real provider + worker |
| production | any | only one on | refuses to start |
| production | incomplete | both on | refuses to start |
| local/test | any | injected by a test | recording double |

The two flags are one decision, refused in halves by both the API and the
chart. Repository writes additionally require a configured App with its
mounted credential references and `https://api.github.com` — a configurable
API origin plus a write credential is an exfiltration primitive.

## Verification

| Gate | Result |
| --- | --- |
| Backend, single process, unfiltered | 1416 passed |
| Backend `-m "not integration"` (CI) | 690 passed |
| Backend `-m integration` (CI) | 753 passed |
| Web unit | 212 passed |
| Contracts | 63 passed |
| Playwright E2E | 45 passed |
| Go agent | 8 packages ok |
| Helm validation (observability + agent) | passed |
| Production chart contract | 127 passed |
| ruff format + check | clean |
| mypy strict | clean |
| Secret scan | no leaks |
| Migration head | `0020`, single head |

Sprint 12A.2b reported CI backend counts of 648 / 752. Those were correct
at that head. This sprint adds tests, so the current numbers are 690 and
753 — recorded above and superseding them.

## Boundaries held

No real GitHub mutation. No branch, commit or pull request. No App
registration or permission change. `github_gitops_pr_enabled` and
`gitops_worker_enabled` off. Datalake `manual_env_review` gate open. No
migration. No deploy, image publish, Helm install or Kubernetes apply. No
`dev → main` promotion. No test deleted, skipped or loosened.
