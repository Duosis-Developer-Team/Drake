# Sprint 12B — the real GitHub provider

Branch `feat/sprint-12b-real-github-provider`, from
`391b5a8c29308016bb4aabbd5585e0cf32a687e7` (`dev`). Migration head `0020`,
unchanged.

**No real GitHub organisation was contacted.** No branch, commit or pull
request exists anywhere as a result of this work. Every provider behaviour
below is proved against a stateful in-process fake of the GitHub write API
that models a real commit graph and records which mutations were actually
applied.

## What shipped

`GitHubPullRequestProvider` — the real implementation. Production gets it
when a GitHub App is configured, and never gets the recording double. Both
GitOps flags stay off by default: shipping the code and turning it on are
separate decisions.

The contract, in one sentence: **the same session, at the same base commit,
with the same content produces at most one branch, one commit and one pull
request** — however many attempts, whatever the network does, and whether
the attempts are sequential or genuinely concurrent.

That is why every step reads before it writes. Writes never go through the
read client's retry loop; a lost response is `github_write_ambiguous` and
the next attempt reconciles by looking.

## The content proposed is the content that was audited

The draft is regenerated at claim time rather than stored — that is what
keeps Drake from holding a copy of a repository file. The cost is that the
generator, the repository projection or the analysis can move underneath a
pending request.

So `gitops_requests.content_digest` is carried into the claim, the
regenerated draft is hashed with SHA-256, and the two are compared byte for
byte immediately before the provider is called. A mismatch is terminal
(`content_digest_mismatch`): a re-analysis produces a request describing
what the repository actually looks like now, which is the honest answer.

| On mismatch | Count |
| --- | --- |
| Installation tokens minted | 0 |
| Provider calls | 0 |
| GitHub HTTP requests | 0 |
| Branches / commits / pull requests | 0 / 0 / 0 |

The persisted digest is never rewritten — it is the evidence.

## What may be pushed at all

`assert_draft_is_safe` **parses**. A line-by-line scan accepted a document
with duplicate keys — a reviewer sees one value and the parser uses
another — and a document that is not a mapping at all. Parsing reuses
`manifest.parse_strict`: the same SafeLoader subclass, duplicate-key
refusal and depth/node bounds the import boundary uses, so the two cannot
drift into disagreeing about what is safe.

The boundary requires, on the exact bytes about to leave: valid YAML, a
root mapping, no duplicate keys, no unsafe YAML tag, depth/node/byte
budgets, exact `apiVersion`, exact `kind`, `spec.repository.provider ==
github`, owner / name / defaultBranch exactly matching the repository being
written to, the stated base commit exactly matching the commit being
proposed onto, exactly the expected `REPLACE_ME` paths — neither missing
nor extra — and the manifest content policy (inline credentials, private
key material, bearer tokens, plaintext endpoints) plus a raw-byte
credential-shape scan.

It deliberately does **not** apply the completed-manifest JSON Schema: a
draft is incomplete by design, and forcing it through that schema would
mean either failing every draft or weakening the schema.

| Draft | Result | HTTP calls |
| --- | --- | --- |
| Real generator output | accepted | — |
| Duplicate mapping key | `draft_refused` | 0 |
| Not a mapping | `draft_refused` | 0 |
| Unsafe YAML tag | `draft_refused` | 0 |
| Wrong `apiVersion` / `kind` / provider | `draft_refused` | 0 |
| Owner / name / defaultBranch mismatch | `draft_refused` | 0 |
| Stated base commit mismatch | `draft_refused` | 0 |
| Missing `REPLACE_ME` | `draft_refused` | 0 |
| Unexpected `REPLACE_ME` | `draft_refused` | 0 |
| `REPLACE_ME`-prefixed guess | `draft_refused` | 0 |
| Policy violation / credential shape | `draft_refused` | 0 |
| Over the write budget | `draft_refused` | 0 |

## Create-or-reuse, proved

| Scenario | Applied branches / commits / pulls |
| --- | --- |
| Fresh proposal | 1 / 1 / 1 |
| Same proposal again | 1 / 1 / 1 (reused) |
| Branch create response lost after it landed | 1 / 1 / 1 |
| Commit response lost after it landed | 1 / 1 / 1 |
| Pull-request response lost after it landed | 1 / 1 / 1 |
| Three sequential passes | 1 / 1 / 1 |
| Existing branch carrying exactly the manifest commit | 0 / 0 / 1 |
| Open pull request over an exact branch | 0 / 0 / 0 (reused) |
| Base branch moved | 0 / 0 / 0 |
| Foreign content on a `drake/` branch | 0 / 0 / 0 |
| Repository id mismatch | 0 / 0 / 0 |
| Archived repository | 0 / 0 / 0 |
| Missing `contents: write` | 0 / 0 / 0 |
| 401 / 403 / 429 / 500 / 503 | 0 / 0 / 0 |
| Path outside `.drake/project.yaml` | 0 / 0 / 0 |
| Draft failing its safety check | 0 / 0 / 0, and zero HTTP calls |
| Draft failing its digest check | 0 / 0 / 0, and zero HTTP calls |

## Branch provenance — what a reused pull request may carry

Matching content is not provenance. A branch can hold exactly the expected
`.drake/project.yaml` *and* somebody else's commit, or *and* a second file,
and a check that only asked "is the manifest right?" would present that as
Drake's proposal.

The invariant: **the pull request Drake creates or reuses carries exactly
one change on top of the reviewed base — `.drake/project.yaml`, with
exactly the proposed content.**

Reuse therefore requires all of: the reviewed base is the exact merge base,
`behind_by == 0`, exactly one commit ahead, that commit changing exactly
one file, that file being the manifest path, and its bytes matching. This
is established through a bounded `compare` read, pinned to commit shas,
**before** the pull-request search is allowed to answer — an open pull
request does not make a foreign commit Drake's work.

| Branch state | Result | Applied |
| --- | --- | --- |
| Absent | created from the reviewed base | 1 / 1 / 1 |
| At the reviewed base, no manifest | manifest committed | 0 / 1 / 1 |
| Exactly the manifest commit | reused | 0 / 0 / 1 |
| Exactly the manifest commit + open PR | same PR reused | 0 / 0 / 0 |
| Manifest commit + a second foreign commit | `branch_conflict` | 0 / 0 / 0 |
| One commit changing the manifest *and* another file | `branch_conflict` | 0 / 0 / 0 |
| Unsafe diff, with an open pull request | `branch_conflict`, PR not reused | 0 / 0 / 0 |
| Unrelated history (no common ancestor) | `branch_conflict` | 0 / 0 / 0 |
| Behind the reviewed base | `branch_conflict` | 0 / 0 / 0 |
| Diverged from the reviewed base | `branch_conflict` | 0 / 0 / 0 |
| Larger than the inspection budget | terminal, refused unread | 0 / 0 / 0 |

## Genuine concurrency

Two providers, one fake, `asyncio.gather`, and a rendezvous that holds every
caller reaching an endpoint until both have arrived — so both have already
done their reads before either writes. The previous sprint's "racing" test
was sequential and said so; it is kept, renamed for what it actually proves
(resumability), and the interleaving below is what covers concurrency.

| Race | Applied branches / commits / pulls |
| --- | --- |
| Both see no branch; both create | 1 / 1 / 1 |
| Both see no manifest; both commit | 0 / 1 / 1 |
| Both see a ready branch; both open a pull request | 0 / 0 / 1 |

A refused write (409/422) is reconciled **in the context of the endpoint
that refused it**, never re-sent:

| Refused write | Reconciliation |
| --- | --- |
| Branch create 409/422 | re-read the ref; continue if it is this proposal, else `branch_conflict` |
| File PUT 409/422 | re-read the file and branch; continue only on an exact match, else `branch_conflict` |
| Pull-request create 409/422 | search again for the exact head/base pair; reuse if found |

## Rate-limit classification

A 429 is rate limiting whatever headers accompany it. Requiring
`x-ratelimit-remaining: 0` made a bare 429 — what a proxy or a secondary
limit answers with — terminal, so Drake abandoned work that would have
succeeded a minute later. A 403 stays terminal unless the **headers** say
otherwise; the body is provider prose and does not steer Drake. `Retry-After`
is read only in its integer-seconds form and clamped.

| Response | Classification |
| --- | --- |
| 429, no headers | retryable, `github_rate_limited` |
| 429 + `Retry-After` | retryable, `github_rate_limited` |
| 429 + `x-ratelimit-remaining: 0` | retryable, `github_rate_limited` |
| 403 + `x-ratelimit-remaining: 0` | retryable, `github_rate_limited` |
| 403 + `Retry-After` | retryable, `github_rate_limited` |
| 403, no rate-limit evidence | terminal, `github_permission_missing` |
| 401 | terminal, `github_permission_missing` |
| 5xx | retryable, `github_unavailable` |

Retryable outcomes inherit the existing lease, timeout, `MAX_ATTEMPTS`,
fencing and cancellation bounds unchanged.

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
| Backend, single process, unfiltered | 1444 passed |
| Backend `-m "not integration"` (CI) | 717 passed |
| Backend `-m integration` (CI) | 754 passed |
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
at that head. This sprint adds tests, so the numbers above supersede them.

## Boundaries held

No real GitHub mutation. No branch, commit or pull request. No App
registration or permission change. `github_gitops_pr_enabled` and
`gitops_worker_enabled` off. Datalake `manual_env_review` gate open. No
migration. No deploy, image publish, Helm install or Kubernetes apply. No
`dev → main` promotion. No test deleted, skipped or loosened.
